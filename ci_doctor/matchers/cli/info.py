"""`ci-doctor matchers info`."""

from pathlib import Path
from urllib.parse import urlsplit

import typer

from ci_doctor.matchers.cli._common import CONFIG, effective, via
from ci_doctor.matchers.registry import Resolved, check_ref, fetch_matcher, redact, session

NAME = "info"


def run(ref: str = typer.Argument(..., metavar="ALIAS/NAME"), config: Path = CONFIG) -> None:
    """Show one matcher or bundle: what it does, where it lives, what a bundle holds."""
    cfg = effective(config)
    seen = Resolved()
    with session() as http:
        parsed = check_ref(ref, cfg, http, seen)
        served, location = seen.indexes[parsed.alias], seen.locations[parsed.alias]
        is_matcher = parsed.name in served.matchers
        entry = served.matchers[parsed.name] if is_matcher else served.bundles[parsed.name]
        typer.echo(f"{ref} ({'matcher' if is_matcher else 'bundle'})")
        typer.echo(f"description: {entry.description}")
        if is_matcher:
            typer.echo(f"classification: {', '.join(entry.classification)}")
            typer.echo(f"role: {fetch_matcher(parsed, seen, http).role}")
        typer.echo(f"registry: {redact(location)}")
        typer.echo(f"path: {entry.path}")
        if url := _history_url(location, entry.path):
            typer.echo(f"history: {url}")
        if not is_matcher:
            members = via(cfg, [ref], http)
            typer.echo("matchers:\n" + "\n".join(f"  {m}" for m in members))


def _history_url(location: str, path: str) -> str | None:
    """A link to a file's history when the registry is hosted on GitHub.

    Args:
        location: The registry's directory URL.
        path: The file, relative to it.

    Returns:
        The commits URL, or None for any other host.
    """
    parts = urlsplit(location)
    segs = [s for s in parts.path.split("/") if s]
    host = parts.hostname or ""
    # ponytail: a github.io site may serve a folder or branch other than the default of the
    # repo it is named after; this links the default branch and the user navigates from there.
    if host == "raw.githubusercontent.com" and len(segs) >= 3:
        owner, repo, branch, *sub = segs
        return f"https://github.com/{owner}/{repo}/commits/{branch}/{'/'.join([*sub, path])}"
    if host.endswith(".github.io"):
        owner = host.removesuffix(".github.io")
        repo = segs[0] if segs else host
        return f"https://github.com/{owner}/{repo}/commits/HEAD/{'/'.join([*segs[1:], path])}"
    return None
