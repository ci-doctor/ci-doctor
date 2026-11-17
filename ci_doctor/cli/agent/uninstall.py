"""`ci-doctor agent uninstall`."""

import typer
from typing_extensions import override

from ci_doctor.cli._console.output.changes import plan, show
from ci_doctor.cli._console.output.message import say
from ci_doctor.cli._console.tui import Cancelled, RichPrompter, interactive, yes_no
from ci_doctor.cli._lib.command import Command
from ci_doctor.harness.install import present, uninstall
from ci_doctor.harness.registry import TARGETS, select
from ci_doctor.harness.targets.base import Location


class UninstallCommand(Command):
    """Remove ci-doctor from coding agents: its entries, and any container they leave empty.

    With no flags, in a terminal, it lists the files and asks before removing.
    With flags, or from a script, it removes without asking.
    """

    name = "uninstall"
    examples = (
        "ci-doctor agent uninstall   # lists the files and asks; from a script, removes",
        "ci-doctor agent uninstall --target cursor --location global",
    )

    @override
    def run(
        self,
        target: str | None = typer.Option(
            None, "--target", "-t", help=f"all (the default), or ids: {','.join(TARGETS)}."
        ),
        location: Location | None = typer.Option(
            None, "--location", "-l", help="local (the default): this project's files. global: your own."
        ),
    ) -> None:
        ask = target is None and location is None and interactive()
        location = location or Location.LOCAL
        agents = present(select(target or "all"), location)
        if not agents:
            say(f"ci-doctor is not in any agent's {location} config; nothing to remove.")
            return
        plan(agents, location, "remove ci-doctor from")
        if ask:
            try:
                if not yes_no(RichPrompter(), "Go ahead?"):
                    say("Nothing removed.")
                    return
            except Cancelled:
                say("Cancelled; nothing removed.")
                return
        show(uninstall(agents, location), location)
