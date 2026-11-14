"""`ci-doctor mcp uninstall`."""

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.outputs.changes import confirm, show
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.harness.install import present, uninstall
from ci_doctor.harness.registry import TARGETS, select
from ci_doctor.harness.targets.base import Location


class UninstallCommand(Command):
    """Remove ci-doctor from coding agents: only what `mcp install` added."""

    name = "uninstall"
    examples = (
        "ci-doctor mcp uninstall",
        "ci-doctor mcp uninstall --target cursor --location global --yes",
    )

    @override
    def run(
        self,
        target: str = typer.Option("all", "--target", "-t", help=f"all, auto, or ids: {','.join(TARGETS)}."),
        location: Location = typer.Option(
            Location.LOCAL, "--location", "-l", help="local: this project's files. global: your own."
        ),
        yes: bool = typer.Option(False, "--yes", "-y", help="Remove without asking."),
    ) -> None:
        agents = present(select(target), location)
        if not agents:
            say(f"ci-doctor is not in any agent's {location} config; nothing to remove.")
            return
        if confirm(agents, location, yes, "remove ci-doctor from"):
            show(uninstall(agents, location), location)
