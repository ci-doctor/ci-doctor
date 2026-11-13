"""Which run a caller means, from a URL, a PR/MR number, a run id, or nothing at all.

`analyze` is handed a run id. A developer asking an agent "why did CI fail?" has
none: they mean the run for what they just pushed. This works that out from the
checkout, and says plainly when the answer is "nothing failed", "still running" or
"you haven't pushed", instead of guessing at an older run.

Read-only throughout (invariant #10): it lists runs and reads git, nothing else.
"""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import RunSummary
from ci_doctor.core.ports import CIProvider
from ci_doctor.providers.git_origin import Remote, head, pushed, remote
from ci_doctor.providers.hosts import host_of, vendor_for_host
from ci_doctor.providers.registry import make_ci_provider, run_url_parser

#: A PR (`#12`) or MR (`!12`) number, as people type them.
_CHANGE = re.compile(r"^[#!](\d+)$")


class Outcome(StrEnum):
    """What resolving found."""

    FOUND = "found"  # runs to analyze
    STILL_RUNNING = "still_running"  # runs to analyze, none finished failing yet
    NO_FAILURE = "no_failure"  # the newest run passed
    NOT_PUSHED = "not_pushed"  # HEAD is not on any remote branch
    NOT_FOUND = "not_found"  # nothing matched: no runs, or a URL that is not ours
    NO_WORKSPACE = "no_workspace"  # no target, and no checkout to read one from


@dataclass(frozen=True)
class Resolution:
    """The runs a target names, or why there are none.

    Attributes:
        outcome: What was found.
        run_ids: The runs to analyze: every failed or still-running run for the
            commit, newest first. Several workflows can build one commit.
        job_id: A single job, when the target named one.
        in_progress: Whether some run is still going, so more jobs may yet fail.
        newest: The run that decided a `no_failure`, for its link.
        earlier_failure: The branch's newest failed run, offered when the current
            commit has nothing to analyze yet. Listed, never analyzed.
        vendor: The CI system, when known.
        repo: The repository, when known.
        notes: How it was decided, for the reader: which remote, which fallback.
    """

    outcome: Outcome
    run_ids: tuple[str, ...] = ()
    job_id: str | None = None
    in_progress: bool = False
    newest: RunSummary | None = None
    earlier_failure: RunSummary | None = None
    vendor: str | None = None
    repo: str | None = None
    notes: tuple[str, ...] = ()


def resolve(target: str | None, cfg: Config, workspace: Path | None) -> tuple[Resolution, CIProvider | None]:
    """Turn what the caller typed into the runs to analyze.

    Args:
        target: A run or job URL, `#<PR>` / `!<MR>`, a run id, or None for "the run
            for my checkout's HEAD".
        cfg: The effective config.
        workspace: The checkout, or None when there is none.

    Returns:
        The resolution, and the adapter to fetch its runs with (None when no
        adapter was built).
    """
    target = (target or "").strip() or None
    if target is not None and "://" in target:
        return _from_url(target, cfg, workspace)
    if target is None and workspace is None:
        return Resolution(
            Outcome.NO_WORKSPACE, notes=("no run named and no checkout to find one from",)
        ), None

    found = _ci_remote(workspace) if workspace is not None else None
    vendor, notes = _vendor(found, cfg)
    repo = found.repo if found is not None else None
    provider = make_ci_provider(cfg, vendor, repo=repo, cwd=workspace)
    if target is None:
        res = _for_head(provider, workspace)
    elif m := _CHANGE.match(target):
        runs = provider.find_runs(mr=m[1])
        res = _classify([r for r in runs if r.sha == runs[0].sha] if runs else [], provider, branch=None)
    else:
        res = Resolution(Outcome.FOUND, run_ids=(target,))
    return replace(res, vendor=vendor, repo=repo, notes=notes + res.notes), provider


def _from_url(url: str, cfg: Config, workspace: Path | None) -> tuple[Resolution, CIProvider | None]:
    """Resolve a pasted run or job URL.

    The URL's host must be one ci-doctor is configured for: the API called is always
    the configured one, never one a URL names.

    Args:
        url: The URL.
        cfg: The effective config.
        workspace: The checkout, if any.

    Returns:
        The resolution, and the adapter when the URL was one of ours.
    """
    host = host_of(url) or ""
    vendor = vendor_for_host(host, cfg)
    if vendor is None:
        note = (
            f"ci-doctor is not configured for {host}: set github.base_url or gitlab.base_url in your config"
        )
        return Resolution(Outcome.NOT_FOUND, notes=(note,)), None
    parser = run_url_parser(vendor)
    ref = parser(url, host) if parser is not None else None
    if ref is None:
        return Resolution(Outcome.NOT_FOUND, vendor=vendor, notes=(f"not a run or job URL: {url}",)), None
    provider = make_ci_provider(cfg, vendor, repo=ref.repo, cwd=workspace)
    run_id = ref.run_id or provider.run_of_job(ref.job_id or "")
    return Resolution(Outcome.FOUND, (run_id,), ref.job_id, vendor=vendor, repo=ref.repo), provider


def _for_head(provider: CIProvider, workspace: Path | None) -> Resolution:
    """The runs for the checkout's HEAD, else the branch's newest failure once it is pushed.

    Args:
        provider: The adapter.
        workspace: The checkout.

    Returns:
        The resolution.
    """
    here = head(workspace)
    if here.sha is None:
        return Resolution(Outcome.NO_WORKSPACE, notes=("the workspace is not a git checkout with a commit",))
    if runs := provider.find_runs(sha=here.sha):
        return _classify(runs, provider, here.branch)
    short = here.sha[:12]
    if not pushed(here.sha, workspace):
        return Resolution(
            Outcome.NOT_PUSHED, notes=(f"HEAD ({short}) is on no remote branch: push it, or name a run",)
        )
    failed = _newest_failure(provider, here.branch) if here.branch is not None else None
    if failed is None:
        return Resolution(Outcome.NOT_FOUND, notes=(f"no run found for HEAD ({short})",))
    note = f"no run for HEAD ({short}) yet; using the newest failed run on {here.branch}"
    return Resolution(Outcome.FOUND, run_ids=(failed.id,), notes=(note,))


def _classify(runs: list[RunSummary], provider: CIProvider, branch: str | None) -> Resolution:
    """Decide what one commit's runs mean.

    Args:
        runs: Every run for one commit, newest first.
        provider: The adapter, to look for an earlier failure on the branch.
        branch: The branch for that look; None for a PR/MR, which has no single branch.

    Returns:
        FOUND with the failed and running runs; STILL_RUNNING when only running ones;
        NO_FAILURE when the newest finished clean.
    """
    if not runs:
        return Resolution(Outcome.NOT_FOUND, notes=("no runs found",))
    failed = [r.id for r in runs if r.status in ("failed", "cancelled")]
    running = [r.id for r in runs if r.status == "running"]
    if failed:
        return Resolution(Outcome.FOUND, run_ids=(*failed, *running), in_progress=bool(running))
    if running:
        earlier = _newest_failure(provider, branch, not_sha=runs[0].sha) if branch is not None else None
        return Resolution(
            Outcome.STILL_RUNNING, run_ids=tuple(running), in_progress=True, earlier_failure=earlier
        )
    return Resolution(Outcome.NO_FAILURE, newest=runs[0])


def _newest_failure(provider: CIProvider, branch: str, not_sha: str | None = None) -> RunSummary | None:
    """The newest failed run on a branch, skipping one commit's runs.

    Args:
        provider: The adapter.
        branch: The branch.
        not_sha: A commit whose runs don't count.

    Returns:
        The run, or None when the branch's recent runs hold no failure.
    """
    return next(
        (r for r in provider.find_runs(branch=branch) if r.status == "failed" and r.sha != not_sha), None
    )


def _ci_remote(workspace: Path) -> Remote | None:
    """The remote whose CI runs this checkout's changes.

    A fork's pull requests run in the upstream repository, so `upstream` wins over
    `origin` when both are on the same host.

    Args:
        workspace: The checkout.

    Returns:
        The remote, or None when there is none.
    """
    origin, upstream = remote("origin", workspace), remote("upstream", workspace)
    if upstream is not None and (origin is None or upstream.host == origin.host):
        return upstream
    return origin


def _vendor(found: Remote | None, cfg: Config) -> tuple[str, tuple[str, ...]]:
    """The CI system for a checkout: from its remote's host, else `ci` from config.

    Args:
        found: The remote, if any.
        cfg: The effective config.

    Returns:
        The vendor, and notes saying which remote and which fallback were used.
    """
    notes = (
        (f"using remote {found.name!r} ({found.repo})",)
        if found is not None and found.name != "origin"
        else ()
    )
    vendor = vendor_for_host(found.host, cfg) if found is not None else None
    if vendor is not None:
        return vendor, notes
    why = f"{found.host} is not a configured CI host" if found is not None else "no git remote"
    return cfg.ci, (*notes, f"{why}; using ci: {cfg.ci} from config")
