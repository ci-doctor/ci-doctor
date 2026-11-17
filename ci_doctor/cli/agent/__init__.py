"""`ci-doctor agent …`: connecting coding agents to ci-doctor's MCP server."""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.agent.install import InstallCommand
from ci_doctor.cli.agent.uninstall import UninstallCommand

COMMANDS: Sequence[Command] = [InstallCommand(), UninstallCommand()]

app = typer.Typer(
    no_args_is_help=True,
    help="Connect coding agents (Claude Code, Cursor, Codex, ...) to ci-doctor's MCP server.",
)
register(app, COMMANDS)
