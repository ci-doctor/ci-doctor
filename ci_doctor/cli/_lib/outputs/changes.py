"""Showing what `mcp install` and `mcp uninstall` will do, and what they did."""

import sys
from pathlib import Path

import typer

from ci_doctor.cli._lib.outputs.message import done, say, skipped
from ci_doctor.errors import SetupError
from ci_doctor.harness.install import Outcome
from ci_doctor.harness.targets.base import Location, Target


def confirm(agents: list[Target], location: Location, yes: bool, verb: str) -> bool:
    """List the files about to change and ask, unless `--yes`.

    Args:
        agents: The agents.
        location: `local` or `global`.
        yes: Skip the question.
        verb: What will happen, e.g. "add ci-doctor to".

    Returns:
        Whether to go ahead.

    Raises:
        SetupError: If asking is impossible (no terminal) and `--yes` was not given.
    """
    say(f"This will {verb} ({location}):", "bold")
    for agent in agents:
        if location in agent.locations:
            say(f"  {agent.name}: {', '.join(_short(p) for p in agent.paths(location))}")
        else:
            say(f"  {agent.name}: skipped, no {location} config", "dim")
    if yes:
        return True
    if not sys.stdin.isatty():
        raise SetupError("not a terminal to ask in; pass --yes")
    return typer.confirm("Go ahead?", default=True)


def show(outcomes: list[Outcome], location: Location) -> None:
    """Print one line per file changed, and the snippet for any file left alone.

    Args:
        outcomes: What happened, per agent.
        location: Where they were installed, for the snippet.

    Raises:
        SetupError: If any file was skipped, so the command exits 1 after doing the rest.
    """
    unsafe = 0
    for outcome in outcomes:
        say(outcome.target.name, "bold")
        if outcome.skipped:
            skipped(f"  skipped: {outcome.skipped}")
            continue
        for change in outcome.changes:
            if change.action == "skipped":
                unsafe += 1
                skipped(f"  left {_short(change.path)} as it was: {change.note}")
            elif change.action in ("absent", "unchanged"):
                skipped(f"  {change.action}: {_short(change.path)}")
            else:
                done(f"  {change.action} {_short(change.path)}")
        if any(c.action == "skipped" for c in outcome.changes):
            say("  Add this by hand:", "yellow")
            say(outcome.target.snippet(location))
    if unsafe:
        raise SetupError(f"{unsafe} file(s) were left as they were; add the config shown above by hand")


def _short(path: Path) -> str:
    """A path relative to the project, or under `~`, for display."""
    for base, label in ((Path.cwd(), "."), (Path.home(), "~")):
        try:
            return f"{label}/{path.relative_to(base)}"
        except ValueError:
            continue
    return str(path)
