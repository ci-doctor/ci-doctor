"""`ci-doctor mcp …`. With no subcommand it serves, so agent configs can just say `ci-doctor mcp`."""

from collections.abc import Sequence
from pathlib import Path

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.mcp.install import InstallCommand
from ci_doctor.cli.mcp.serve import PROJECT_HELP, ServeCommand
from ci_doctor.cli.mcp.uninstall import UninstallCommand

COMMANDS: Sequence[Command] = [ServeCommand(), InstallCommand(), UninstallCommand()]

app = typer.Typer(help="Serve ci-doctor to coding agents over MCP, and connect agents to it.")
register(app, COMMANDS)


@app.callback(invoke_without_command=True)
def _serve_by_default(
    ctx: typer.Context, project: Path | None = typer.Option(None, "--project", help=PROJECT_HELP)
) -> None:
    """Serve ci-doctor to coding agents over MCP, and connect agents to it."""
    if ctx.invoked_subcommand is None:
        ServeCommand().run(project)
