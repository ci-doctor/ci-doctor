"""`ci-doctor agent install`."""

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.outputs.changes import confirm, preview, show
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.harness.entry import on_path
from ci_doctor.harness.install import install
from ci_doctor.harness.registry import TARGETS, select
from ci_doctor.harness.targets.base import Location


class InstallCommand(Command):
    """Connect coding agents to ci-doctor's MCP server: Claude Code, Cursor, Codex, and more.

    Writes each agent's MCP config, a short instructions block, and (Claude Code only)
    an allow list for the read-only tools. Running it again changes nothing.
    `--dry-run` shows each file and the config that would go in it, and writes nothing.
    """

    name = "install"
    examples = (
        "ci-doctor agent install   # the agents found here, in this project",
        "ci-doctor agent install --target claude,cursor --location global --yes",
        "ci-doctor agent install --target codex --location global --dry-run",
    )

    @override
    def run(
        self,
        target: str = typer.Option(
            "auto", "--target", "-t", help=f"auto (found here), all, or ids: {','.join(TARGETS)}."
        ),
        location: Location = typer.Option(
            Location.LOCAL, "--location", "-l", help="local: this project's files. global: your own."
        ),
        yes: bool = typer.Option(False, "--yes", "-y", help="Write without asking."),
        permissions: bool = typer.Option(
            True, "--permissions/--no-permissions", help="Auto-allow the read-only tools in Claude Code."
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show the files and the config that would be added; write nothing."
        ),
    ) -> None:
        agents = select(target)
        if not agents:
            say("no coding agents found here; name them with --target, e.g. --target claude", "yellow")
            return
        if dry_run:
            preview(agents, location)
            return
        if not confirm(agents, location, yes, "add ci-doctor to"):
            return
        show(install(agents, location, permissions=permissions), location)
        if not on_path():
            say(
                "Warning: `ci-doctor` is not on your PATH, so agents cannot start it. "
                "Install it with `uv tool install ci-doctorr` or `pipx install ci-doctorr`.",
                "yellow",
            )
        say(
            "Restart your agents to load ci-doctor. To analyze live runs, set CI_DOCTOR_GITHUB_TOKEN "
            "or CI_DOCTOR_GITLAB_TOKEN where the agent runs."
        )
