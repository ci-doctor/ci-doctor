"""Showing what `agent install` and `agent uninstall` will do, and what they did."""

from pathlib import Path

from ci_doctor.agent.harness.install import Outcome
from ci_doctor.agent.harness.targets.base import Location, Target
from ci_doctor.cli._console.output.message import done, say, skipped
from ci_doctor.errors import SetupError


def plan(agents: list[Target], location: Location, verb: str) -> None:
    """List the files about to change; the command decides whether to ask first.

    Args:
        agents: The agents.
        location: `local` or `global`.
        verb: What will happen, e.g. "add ci-doctor to".
    """
    say(f"This will {verb} ({location}):", "bold")
    for agent in agents:
        if location in agent.locations:
            say(f"  {agent.name}: {', '.join(_short(p) for p in agent.paths(location))}")
        else:
            say(f"  {agent.name}: skipped, no {location} config", "dim")


def preview(agents: list[Target], location: Location) -> None:
    """Show what `agent install` would write for each agent, writing nothing (`--dry-run`).

    Args:
        agents: The agents.
        location: `local` or `global`.
    """
    say(f"Dry run, nothing is written. ci-doctor would be added to ({location}):", "bold")
    for agent in agents:
        say(agent.name, "bold")
        if location not in agent.locations:
            skipped(f"  skipped: no {location} config; use --location global")
            continue
        for path in agent.paths(location):
            say(f"  {_short(path)}")
        if agent.requires:
            say(f"  needs {agent.requires}", "yellow")
        say(agent.snippet(location))


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
        if outcome.target.requires:
            say(f"  needs {outcome.target.requires}", "yellow")
        if any(c.action == "skipped" for c in outcome.changes):
            say("  Add this by hand:", "yellow")
            say(outcome.target.snippet(location))
    if unsafe:
        raise SetupError(f"{unsafe} file(s) were left as they were; add the config shown above by hand")


def _short(path: Path) -> str:
    """A path relative to the project, or under `~`, for display."""
    for base, label in ((Path.cwd(), "."), (Path.home(), "~")):
        try:
            return f"{label}/{path.relative_to(base).as_posix()}"
        except ValueError:
            continue
    return str(path)
