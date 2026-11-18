"""ci-doctor's command line, laid out like the command tree (GUIDELINES §2.1)."""

import logging
from collections.abc import Sequence
from pathlib import Path

import typer
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler

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
from ci_doctor.errors import SetupError

log = logging.getLogger("ci_doctor.cli")
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
    config_path: list[Path] = typer.Option(
        None,
        "--config",
        "-f",
        help="Path to a .ci-doctor.yml, for every command. Repeatable — later files override earlier ones.",
    ),
    write_to: Path | None = typer.Option(
        None,
        "--write-to",
        help="The file commands that edit the config write (read too, once it exists). Default: your user config.",
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Bare text for scripts: no tables, no colour, no pager."
    ),
    no_color: bool = typer.Option(
        False, "--no-color", help="Disable coloured output (also honours NO_COLOR)."
    ),
    log_level: terminal.LogLevel | None = typer.Option(
        None,
        "--log-level",
        help="How much to log on stderr: debug, info (the default), warning or error. Env: CI_DOCTOR_LOG_LEVEL.",
        case_sensitive=False,
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Explain why a CI pipeline failed (read-only postmortem)."""
    # Undone when the command ends, so nothing leaks into the next invocation in this process.
    ctx.call_on_close(context.use(config_path or [], write_to))
    ctx.call_on_close(terminal.use(plain=plain, no_color=no_color))
    terminal.configure_logging(log_level, colour=not (no_color or plain))


def main() -> None:
    """Console-script entry point. Any failure exits 1; `analyze` keeps invariant #3 itself."""
    # NullHighlighter: rich falls back to ReprHighlighter on highlighter=None.
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        force=True,
        handlers=[
            RichHandler(
                console=terminal.LOG_CONSOLE,
                show_time=False,
                show_path=False,
                markup=False,
                highlighter=NullHighlighter(),
            )
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per registry file fetched
    try:
        app()
    except SetupError as exc:
        log.error(exc)
        raise SystemExit(1) from None
    except SystemExit as exc:
        raise SystemExit(0 if exc.code in (0, None) else 1) from None
    except Exception as exc:  # noqa: BLE001 - a crash is one log line and exit 1, not a traceback
        log.error(f"internal error: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from None
