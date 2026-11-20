"""ci-doctor's command line, laid out like the command tree (GUIDELINES §2.1)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from ci_doctor import __version__
from ci_doctor.cli._console import terminal
from ci_doctor.cli._lib import context
from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.agent import app as agent_app
from ci_doctor.cli.analyze import AnalyzeCommand
from ci_doctor.cli.cache import app as cache_app
from ci_doctor.cli.config import ConfigCommand
from ci_doctor.cli.matchers import app as matchers_app
from ci_doctor.cli.mcp import McpCommand
from ci_doctor.cli.registry import app as registry_app
from ci_doctor.cli.report import ReportCommand

COMMANDS: Sequence[Command] = [AnalyzeCommand(), ConfigCommand(), ReportCommand(), McpCommand()]

app = typer.Typer(add_completion=False, help="Explain why a CI pipeline failed (read-only postmortem).")
register(app, COMMANDS)
app.add_typer(matchers_app, name="matchers")
app.add_typer(registry_app, name="registry")
app.add_typer(cache_app, name="cache")
app.add_typer(agent_app, name="agent")


def _version_callback(value: bool) -> None:
    """Print the version and exit, before any other option is processed.

    Args:
        value: Whether `--version` was passed.

    Raises:
        typer.Exit: Always, when `value` is true.
    """
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    config_path: Annotated[
        list[Path] | None,
        typer.Option(
            "--config",
            "-f",
            help="Path to a .ci-doctor.yml, for every command. Repeatable — later files override earlier ones.",
        ),
    ] = None,
    write_to: Annotated[
        Path | None,
        typer.Option(
            "--write-to",
            help="The file commands that edit the config write (read too, once it exists). Default: your user config.",
        ),
    ] = None,
    plain: Annotated[
        bool, typer.Option("--plain", help="Bare text for scripts: no tables, no colour, no pager.")
    ] = False,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Disable coloured output (also honours NO_COLOR).")
    ] = False,
    log_level: Annotated[
        terminal.LogLevel | None,
        typer.Option(
            "--log-level",
            help="How much to log on stderr: debug, info (the default), warning or error. Env: CI_DOCTOR_LOG_LEVEL.",
            case_sensitive=False,
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """Explain why a CI pipeline failed (read-only postmortem)."""
    # Undone when the command ends, so nothing leaks into the next invocation in this process.
    ctx.call_on_close(context.use(config_path or [], write_to))
    ctx.call_on_close(terminal.use(plain=plain, no_color=no_color))
    terminal.configure_logging(log_level, colour=not (no_color or plain))


def main() -> None:
    """Console-script entry point: a usage error exits 1 like any other failure (invariant #3)."""
    try:
        app()
    except SystemExit as exc:
        raise SystemExit(0 if exc.code in (0, None) else 1) from None
