"""Which adapter, and which segmenter, serve a vendor name.

One place to register a provider, so adding one is a line here rather than an
edit inside a command. Both tables map a name to a dotted path and import it
only when that name is actually asked for: `core/`, the offline replay path and
`ci-doctor config` must never drag a vendor SDK into the process.

The two tables are keyed differently on purpose. Adapters are keyed by vendor
because a vendor may serve as the CI system, the git host, or both. Segmenters
are keyed by **CI system only** — log framing is whatever the *runner* printed,
which is also why one segmenter can serve several CI systems (Forgejo and Gitea
Actions both run act_runner, which emits GitHub's `##[group]` markers).
"""

import logging
import re
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import RunRef
from ci_doctor.core.ports import CIProvider, LogSegmenter, SCMProvider

log = logging.getLogger("ci_doctor.providers")

#: Vendor name -> adapter. Which ports it implements is discovered from the class,
#: not declared here: a vendor that is both a CI system and a git host (GitLab,
#: GitHub) implements both over one client.
ADAPTERS: dict[str, str] = {
    "gitlab": "ci_doctor.providers.gitlab.provider:GitLabProvider",
    "github": "ci_doctor.providers.github.provider:GitHubProvider",
}

#: CI system -> segmenter for the log format its runner emits.
SEGMENTERS: dict[str, str] = {
    "gitlab": "ci_doctor.providers.gitlab.segmenter:GitLabSegmenter",
    "github": "ci_doctor.providers.github.segmenter:GitHubSegmenter",
}

#: CI system -> a pure `parse_run_url(url, web_host) -> RunRef | None` for its web URLs.
URL_PARSERS: dict[str, str] = {
    "gitlab": "ci_doctor.providers.gitlab.urls:parse_run_url",
    "github": "ci_doctor.providers.github.urls:parse_run_url",
}

#: Segmenter used when the configured CI system has none of its own.
_FALLBACK_SEGMENTER = SEGMENTERS["github"]

#: What a runner prints that no other does, for replaying a log file where `ci` is a
#: default nobody chose. Unanchored on purpose: GitHub prefixes every line with a
#: timestamp and GitLab wraps its markers in ANSI, so neither sits at column 0.
_LOG_SIGNATURES: dict[str, re.Pattern[str]] = {
    "github": re.compile(r"##\[(?:group|endgroup|error|warning)\]"),
    "gitlab": re.compile(r"section_(?:start|end):\d+:"),
}


def _load(spec: str) -> Any:  # noqa: ANN401 - an import resolved by name cannot be typed
    """Import a `"module:attribute"` target.

    Args:
        spec: A dotted module path, a colon, and the attribute name.

    Returns:
        The attribute. Imported here and not at module scope so a vendor's SDK
        is only ever loaded by a run that actually reaches that vendor.
    """
    module, _, name = spec.partition(":")
    return getattr(import_module(module), name)


def make_adapter(
    vendor: str, cfg: Config, *, repo: str | None = None, cwd: Path | None = None
) -> CIProvider | SCMProvider | None:
    """Build the adapter for one vendor, whichever ports it implements.

    Args:
        vendor: The vendor name from `ci` or `scm`.
        cfg: The effective config.
        repo: The repository, when the caller knows it; else the adapter finds it.
        cwd: The checkout to read the git origin from, or None for this process's.

    Returns:
        The adapter, or None when no adapter exists for that name. The caller
        checks which ports it got.
    """
    spec = ADAPTERS.get(vendor)
    return _load(spec)(cfg, repo=repo, cwd=cwd) if spec else None


def make_ci_provider(
    cfg: Config, vendor: str | None = None, *, repo: str | None = None, cwd: Path | None = None
) -> CIProvider:
    """Build the adapter that reads the run, named by `config.ci`.

    Args:
        cfg: The effective config.
        vendor: The CI system, when the caller worked it out (from a URL or a git
            remote). Defaults to `config.ci`.
        repo: The repository, when the caller knows it; else the adapter finds it.
        cwd: The checkout to read the git origin from, or None for this process's.

    Returns:
        A :class:`~ci_doctor.core.ports.CIProvider`.

    Raises:
        ValueError: When no adapter reads that CI system.
    """
    name = vendor or cfg.ci
    adapter = make_adapter(name, cfg, repo=repo, cwd=cwd)
    if not isinstance(adapter, CIProvider):
        raise ValueError(f"unsupported CI system: {name}")  # noqa: TRY004 - an unknown name, not a type error
    return adapter


def run_url_parser(vendor: str) -> Callable[[str, str], RunRef | None] | None:
    """The URL parser for a CI system's web links.

    Args:
        vendor: The CI system.

    Returns:
        `parse_run_url(url, web_host)`, or None when the CI system has no parser.
    """
    spec = URL_PARSERS.get(vendor)
    return _load(spec) if spec else None


def make_scm_provider(cfg: Config, ci_provider: object = None) -> SCMProvider | None:
    """Build the adapter that posts the note, named by `config.scm`.

    Args:
        cfg: The effective config.
        ci_provider: The already-connected CI adapter. Reused when the same
            vendor serves both roles — GitLab's pipelines and merge requests are
            one API behind one client, and connecting twice would mean a second
            token read and a second version probe for nothing.

    Returns:
        An :class:`~ci_doctor.core.ports.SCMProvider`, or None when the note has
        nowhere to go — which is not an error, the report still lands in the
        terminal and the artifacts.
    """
    vendor = cfg.scm_vendor
    if vendor == "none":
        return None
    if vendor == cfg.ci and isinstance(ci_provider, SCMProvider):
        return ci_provider
    adapter = make_adapter(vendor, cfg)
    if not isinstance(adapter, SCMProvider):
        log.debug(f"no git-host adapter for {vendor!r}; the note has nowhere to go")
        return None
    return adapter


def segmenter_for(ci: str) -> LogSegmenter:
    """Build the log segmenter for one CI system, by name.

    Args:
        ci: The CI system's name, matching a `SEGMENTERS` key.

    Returns:
        A :class:`~ci_doctor.core.ports.LogSegmenter`, defaulting to GitLab's —
        offline replay of a bare log has no run metadata to go on.
    """
    return _load(SEGMENTERS.get(ci, _FALLBACK_SEGMENTER))()


def make_segmenter(cfg: Config) -> LogSegmenter:
    """Build the log segmenter for the configured CI system.

    Args:
        cfg: The effective config. Read for `ci` and never for `scm`: the log was
            framed by whoever *ran* the job, not by wherever the code lives.

    Returns:
        A :class:`~ci_doctor.core.ports.LogSegmenter`.
    """
    return segmenter_for(cfg.ci)


def segmenter_for_log(raw_log: str, fallback: str) -> LogSegmenter:
    """Pick the segmenter by what the log actually looks like.

    Offline replay has no run metadata, so `ci` is whatever the config happens to
    say — usually the default, which the user never chose. Reading a GitLab trace
    with GitHub's segmenter is not a near miss: no marker matches, every line
    lands in the synthetic preamble, and attribution answers "no signal". So the
    log is asked instead, and the config is only the tie-breaker.

    Args:
        raw_log: The captured job log.
        fallback: CI system to use when nothing in the log identifies a runner —
            normally `config.ci`.

    Returns:
        A :class:`~ci_doctor.core.ports.LogSegmenter`.
    """
    for ci, signature in _LOG_SIGNATURES.items():
        if signature.search(raw_log):
            log.debug(f"log looks like {ci} output")
            return segmenter_for(ci)
    log.debug(f"no runner signature in the log; falling back to ci={fallback!r}")
    return segmenter_for(fallback)
