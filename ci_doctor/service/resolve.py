"""Which run a caller means, from a URL, a PR/MR number, a run id, or nothing at all.

`analyze` is handed a run id. A developer asking an agent "why did CI fail?" has
none: they mean the run for what they just pushed. This works that out from the
checkout, and says plainly when the answer is "nothing failed", "still running" or
"you haven't pushed", instead of guessing at an older run.

Read-only throughout (invariant #10): it lists runs and reads git, nothing else.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import RunQuery, RunSummary
from ci_doctor.core.ports import CIProvider
from ci_doctor.providers.git_origin import Remote, head, pushed, remote
from ci_doctor.providers.hosts import host_of, vendor_for_host
from ci_doctor.providers.registry import make_ci_provider, run_url_parser

log = logging.getLogger("ci_doctor.service.resolve")

#: How many runs to look through for a commit, a branch or a PR.
_LOOKBACK = 20

#: A PR (`#12`) or MR (`!12`) number, as people type them.
_CHANGE = re.compile(r"^[#!](\d+)$")

#: A provider factory: `make_ci_provider`'s signature, injectable for tests.
ProviderFactory = Callable[..., CIProvider]


class Outcome(StrEnum):
    """What resolving found."""

    FOUND = "found"  # runs to analyze
    STILL_RUNNING = "still_running"  # runs to analyze, none finished failing yet
    NO_FAILURE = "no_failure"  # the newest run passed
    NOT_PUSHED = "not_pushed"  # HEAD is not on any remote branch
    NOT_FOUND = "not_found"  # nothing matched: no runs, or a URL that is not ours
    NO_WORKSPACE = "no_workspace"  # no target, and no checkout to read one from


@dataclass
class Resolution:
    """The runs a target names, or why there are none.

    Attributes:
        outcome: What was found.
        vendor: The CI system, when known.
        repo: The repository, when known.
        run_ids: The runs to analyze: every failed or still-running run for the
            commit, newest first. Several workflows can build one commit.
        job_id: A single job, when the target named one.
        in_progress: Whether some run is still going, so more jobs may yet fail.
        newest: The run that decided a `no_failure`, for its link.
        earlier_failure: The newest failed run on the branch, offered when the
            current commit has nothing to analyze yet. Listed, never analyzed.
        notes: How it was decided, for the reader: which remote, which fallback.
    """

    outcome: Outcome
    vendor: str | None = None
    repo: str | None = None
    run_ids: list[str] = field(default_factory=list)
    job_id: str | None = None
    in_progress: bool = False
    newest: RunSummary | None = None
    earlier_failure: RunSummary | None = None
    notes: list[str] = field(default_factory=list)


def resolve(
    target: str | None,
    cfg: Config,
    workspace: Path | None,
    make_provider: ProviderFactory = make_ci_provider,
) -> tuple[Resolution, CIProvider | None]:
    """Turn what the caller typed into the runs to analyze.

    Args:
        target: A run or job URL, `#<PR>` / `!<MR>`, a run id, or None for "the run
            for my checkout's HEAD".
        cfg: The effective config. Load it with `load_untrusted_repo_config` when
            the workspace is not the caller's own choice.
        workspace: The checkout, or None when there is none.
        make_provider: Builds the adapter; tests inject a fake.

    Returns:
        The resolution, and the connected adapter to fetch the runs with (None
        when nothing was found to fetch).
    """
    target = (target or "").strip() or None
    if target is not None and "://" in target:
        return _from_url(target, cfg, workspace, make_provider)
    if target is None and workspace is None:
        return Resolution(Outcome.NO_WORKSPACE, notes=["no run named and no checkout to find one from"]), None

    found = _repo_remote(workspace) if workspace is not None else None
    vendor, notes = _vendor(found, cfg)
    res = Resolution(Outcome.NOT_FOUND, vendor=vendor, repo=found.repo if found else None, notes=notes)
    if found is not None and found.name != "origin":
        res.notes.append(f"using remote {found.name!r} ({found.repo}): origin is a fork of it")
    provider = make_provider(cfg, vendor, repo=res.repo, cwd=workspace)

    if target is not None and (m := _CHANGE.match(target)):
        runs = provider.find_runs(RunQuery(mr=m[1], limit=_LOOKBACK))
        return _classify(res, _newest_commit(runs), provider, branch=None), provider
    if target is not None:
        res.outcome, res.run_ids = Outcome.FOUND, [target]
        return res, provider
    return _from_head(res, provider, workspace), provider


def _from_url(
    url: str, cfg: Config, workspace: Path | None, make_provider: ProviderFactory
) -> tuple[Resolution, CIProvider | None]:
    """Resolve a pasted run or job URL.

    The URL's host must be one ci-doctor is configured for. That is what keeps a
    token on the user's own hosts: the API called is always the configured one,
    never one a URL names.

    Args:
        url: The URL.
        cfg: The effective config.
        workspace: The checkout, if any; only for the adapter's fallbacks.
        make_provider: Builds the adapter.

    Returns:
        The resolution and adapter, or NOT_FOUND and None.
    """
    host = host_of(url) or ""
    vendor = vendor_for_host(host, cfg)
    if vendor is None:
        hint = f"ci-doctor is not configured for {host}: set github.base_url or gitlab.base_url in your user config"
        return Resolution(Outcome.NOT_FOUND, notes=[hint]), None
    parser = run_url_parser(vendor)
    ref = parser(url, host) if parser else None
    if ref is None:
        return Resolution(Outcome.NOT_FOUND, vendor=vendor, notes=[f"not a run or job URL: {url}"]), None
    provider = make_provider(cfg, vendor, repo=ref.repo, cwd=workspace)
    run_id = ref.run_id or provider.run_of_job(ref.job_id or "")
    return Resolution(
        Outcome.FOUND, vendor=vendor, repo=ref.repo, run_ids=[run_id], job_id=ref.job_id
    ), provider


def _from_head(res: Resolution, provider: CIProvider, workspace: Path | None) -> Resolution:
    """Resolve "the run for my HEAD": its commit's runs, else the branch's newest failure.

    Args:
        res: The resolution so far: vendor and repo known.
        provider: The connected adapter.
        workspace: The checkout.

    Returns:
        The finished resolution.
    """
    here = head(workspace)
    if here.sha is None:
        res.outcome = Outcome.NO_WORKSPACE
        res.notes.append("the workspace is not a git checkout with a commit")
        return res
    runs = provider.find_runs(RunQuery(sha=here.sha, limit=_LOOKBACK))
    if runs:
        return _classify(res, runs, provider, here.branch)
    if not pushed(here.sha, workspace):
        res.outcome = Outcome.NOT_PUSHED
        res.notes.append(f"HEAD ({here.sha[:12]}) is on no remote branch: push it, or name a run")
        return res
    if here.branch is not None:
        failed = _newest_failure(provider, here.branch)
        if failed is not None:
            res.outcome, res.run_ids = Outcome.FOUND, [failed.id]
            res.notes.append(
                f"no run for HEAD ({here.sha[:12]}) yet; using the newest failed run on {here.branch}"
            )
            return res
    res.notes.append(f"no run found for HEAD ({here.sha[:12]})")
    return res


def _classify(
    res: Resolution, runs: list[RunSummary], provider: CIProvider, branch: str | None
) -> Resolution:
    """Decide what one commit's runs mean.

    Args:
        res: The resolution so far.
        runs: Every run for one commit, newest first.
        provider: The adapter, to look for an earlier failure on the branch.
        branch: The branch, for that look; None for a PR/MR target.

    Returns:
        The resolution: FOUND with the failed and running runs, STILL_RUNNING
        when only running ones, NO_FAILURE when the newest finished clean.
    """
    if not runs:
        res.notes.append("no runs found")
        return res
    failed = [r for r in runs if r.status in ("failed", "cancelled")]
    running = [r for r in runs if r.status == "running"]
    res.in_progress = bool(running)
    if failed or running:
        res.outcome = Outcome.FOUND if failed else Outcome.STILL_RUNNING
        res.run_ids = [r.id for r in failed + running]
        if not failed and branch is not None:
            res.earlier_failure = _newest_failure(provider, branch, not_sha=runs[0].sha)
        return res
    res.outcome, res.newest = Outcome.NO_FAILURE, runs[0]
    return res


def _newest_commit(runs: list[RunSummary]) -> list[RunSummary]:
    """Keep only the runs for the newest commit in a newest-first list.

    Args:
        runs: Runs, newest first.

    Returns:
        The runs sharing the first run's commit.
    """
    return [r for r in runs if r.sha == runs[0].sha] if runs else []


def _newest_failure(provider: CIProvider, branch: str, not_sha: str | None = None) -> RunSummary | None:
    """The newest failed run on a branch, optionally skipping one commit's runs.

    Args:
        provider: The adapter.
        branch: The branch.
        not_sha: A commit whose runs don't count.

    Returns:
        The run, or None when the lookback holds no failure.
    """
    runs = provider.find_runs(RunQuery(branch=branch, limit=_LOOKBACK))
    return next((r for r in runs if r.status == "failed" and r.sha != not_sha), None)


def _repo_remote(workspace: Path) -> Remote | None:
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


def _vendor(found: Remote | None, cfg: Config) -> tuple[str, list[str]]:
    """The CI system for a checkout: from its remote's host, else `ci` from config.

    Args:
        found: The remote, if any.
        cfg: The effective config.

    Returns:
        The vendor, and a note when it was a fallback.
    """
    vendor = vendor_for_host(found.host, cfg) if found is not None else None
    if vendor is not None:
        return vendor, []
    why = f"{found.host} is not a configured CI host" if found is not None else "no git remote"
    return cfg.ci, [f"{why}; using ci: {cfg.ci} from config"]
