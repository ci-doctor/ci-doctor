"""Running `install` or `uninstall` across the chosen agents."""

from collections.abc import Callable
from dataclasses import dataclass, field

from ci_doctor.harness.targets.base import Location, Target
from ci_doctor.lib.edit import Change


@dataclass(frozen=True)
class Outcome:
    """What happened for one agent.

    Attributes:
        target: The agent.
        changes: One per file touched; empty when the agent was skipped.
        skipped: Why the whole agent was skipped, if it was.
    """

    target: Target
    changes: list[Change] = field(default_factory=list)
    skipped: str = ""


def install(targets: list[Target], location: Location, *, permissions: bool) -> list[Outcome]:
    """Add ci-doctor to each agent.

    Args:
        targets: The agents.
        location: `local` or `global`.
        permissions: Also auto-allow the read-only tools where the agent supports it.

    Returns:
        One outcome per agent.
    """
    return _each(targets, location, lambda t: t.install(location, permissions=permissions))


def present(targets: list[Target], location: Location) -> list[Target]:
    """The targets that have something of ci-doctor's at `location`.

    Args:
        targets: The agents.
        location: `local` or `global`.

    Returns:
        Those `uninstall` would change.
    """
    return [t for t in targets if location in t.locations and t.present(location)]


def uninstall(targets: list[Target], location: Location) -> list[Outcome]:
    """Remove ci-doctor from each agent.

    Args:
        targets: The agents.
        location: `local` or `global`.

    Returns:
        One outcome per agent.
    """
    return _each(targets, location, lambda t: t.uninstall(location))


def _each(targets: list[Target], location: Location, run: Callable[[Target], list[Change]]) -> list[Outcome]:
    outcomes = []
    for target in targets:
        if location not in target.locations:
            outcomes.append(Outcome(target, skipped=f"no {location} config; use --location global"))
        else:
            outcomes.append(Outcome(target, run(target)))
    return outcomes
