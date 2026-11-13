"""Repository identity from the local git checkout.

ci-doctor also runs on a laptop, where none of a CI system's predefined variables
exist. Rather than making the user export `GITHUB_REPOSITORY` / `CI_PROJECT_ID`
by hand, adapters fall back to whatever `origin` points at — and say so, loudly,
because a guessed repository is worth one warning line.

Every lookup takes the checkout to read as an explicit `cwd`. The CLI runs in the
project, so `None` (the process's own directory) is right there; a long-lived
process serving several projects, like the MCP server, is not guaranteed to start
in any of them, and must say which one it means.

Shared by every adapter, so the guess is parsed once and identically. Lives here
rather than in `core/` only because it is I/O; it names no provider.
"""

import logging
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("ci_doctor.git")


@dataclass(frozen=True)
class Remote:
    """One git remote, parsed.

    Attributes:
        name: The remote's name (`origin`, `upstream`).
        host: Its hostname, lower-cased, without port or user. What a token's
            destination is checked against, and what names the CI vendor.
        repo: The repository path: "owner/name", or "group/sub/project" with
            GitLab's nested groups kept whole.
    """

    name: str
    host: str
    repo: str


@dataclass(frozen=True)
class Head:
    """Where the checkout's `HEAD` is.

    Attributes:
        branch: The checked-out branch, or None when `HEAD` is detached.
        sha: The full commit id, or None outside a checkout or before the first commit.
    """

    branch: str | None
    sha: str | None


def parse_remote_url(url: str) -> tuple[str, str] | None:
    """Split a remote URL into its host and repository path.

    Two shapes: "https://host/group/project" (or `ssh://`, `git://`) and the
    scp-style "git@host:group/project". Splitting on the path (not a regex over
    the tail) keeps GitLab's nested groups.

    Args:
        url: The remote URL as git prints it.

    Returns:
        ``(host, repo)``, or None when either part is missing.
    """
    url = url.strip().removesuffix(".git")
    if "://" in url:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    else:
        authority, _, path = url.partition(":")
        host = authority.rpartition("@")[2]
    repo = path.strip("/")
    host = host.lower()
    return (host, repo) if host and repo else None


def remote(name: str = "origin", cwd: Path | str | None = None) -> Remote | None:
    """Read and parse one remote of a checkout.

    Args:
        name: The remote to read.
        cwd: The checkout, or None for the process's own directory.

    Returns:
        The remote, or None outside a checkout, with no such remote, or with a
        URL that names no host and repository.
    """
    url = _git(["remote", "get-url", name], cwd)
    parsed = parse_remote_url(url) if url else None
    return Remote(name, *parsed) if parsed else None


def head(cwd: Path | str | None = None) -> Head:
    """Read where a checkout's `HEAD` is. Never cached: it moves with every commit.

    Args:
        cwd: The checkout, or None for the process's own directory.

    Returns:
        The branch and commit; either is None when git cannot say.
    """
    return Head(
        branch=_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd), sha=_git(["rev-parse", "HEAD"], cwd)
    )


def pushed(sha: str, cwd: Path | str | None = None) -> bool:
    """Whether any remote-tracking branch contains a commit, as far as this checkout knows.

    Read from the local remote-tracking refs, without fetching: a commit pushed from
    another clone reads as unpushed until the next fetch. Good enough to tell
    "you haven't pushed yet" from "CI has not picked it up".

    Args:
        sha: The commit.
        cwd: The checkout, or None for the process's own directory.

    Returns:
        True when some `refs/remotes/*` branch contains it.
    """
    return _git(["branch", "--remotes", "--contains", sha], cwd) is not None


@cache
def origin_repo(env_var: str, cwd: Path | str | None = None) -> str | None:
    """Derive "owner/name" (or "group/sub/project") from the `origin` remote.

    Args:
        env_var: Name of the variable that should have carried this, used in the
            warning so the user knows what to set to silence it.
        cwd: The checkout, or None for the process's own directory.

    Returns:
        The repository path, or None outside a checkout / with no usable origin.
        Cached per checkout: the answer cannot change mid-run, and neither should
        the warning repeat once per job log.
    """
    found = remote("origin", cwd)
    if found is None:
        return None
    log.warning(f"{env_var} is not set; using {found.repo!r} from git origin")
    return found.repo


def _git(args: list[str], cwd: Path | str | None) -> str | None:
    """Run one read-only git command and return its output.

    Args:
        args: The git subcommand and its arguments.
        cwd: Where to run it, or None for the process's own directory.

    Returns:
        Stripped stdout, or None when git is missing, fails, hangs, or prints nothing.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - fixed read-only subcommands, never user input
            ["git", *args],  # noqa: S607 — git off PATH is the point
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,  # a missing remote is an answer, not an error
        )
    except (OSError, subprocess.SubprocessError) as exc:  # no git, no such dir, hung command
        log.debug(f"git {args[0]} failed: {exc}")
        return None
    out = proc.stdout.strip() if proc.returncode == 0 else ""
    return out or None
