"""ci-doctor's command line: one module per command, one package per command group.

The tree mirrors the commands: `cli/analyze.py` is `ci-doctor analyze`,
`cli/matchers/registry/ping.py` is `ci-doctor matchers registry ping`. A group's
`__init__.py` holds its typer app and registers its commands; a command is a module
holding one :class:`~ci_doctor.cli._lib.command.Command`. What commands share lives in
`_lib/`. Nothing here does the work: a command parses, calls `service/` (or the
package that owns it) and prints.

A broken setup (:class:`~ci_doctor.errors.SetupError`, bad usage) or a crash exits 1,
like any CLI. Invariant #3 belongs to `analyze` alone: it must never change a
pipeline's outcome, so it turns its own crashes into exit 0 (`cli/analyze.py`).
"""

import logging
from collections.abc import Sequence
from pathlib import Path

import typer
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler

from ci_doctor import __version__
from ci_doctor.cli._lib import context, terminal
from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.analyze import AnalyzeCommand
from ci_doctor.cli.config import ConfigCommand
from ci_doctor.cli.matchers import app as matchers_app
from ci_doctor.cli.report import ReportCommand
from ci_doctor.errors import SetupError

log = logging.getLogger("ci_doctor.cli")
COMMANDS: Sequence[Command] = [AnalyzeCommand(), ConfigCommand(), ReportCommand()]

app = typer.Typer(add_completion=False, help="Explain why a CI pipeline failed (read-only postmortem).")
register(app, COMMANDS)
app.add_typer(matchers_app, name="matchers")


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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging, for every command."),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
) -> None:
    """Explain why a CI pipeline failed (read-only postmortem)."""
    ctx.meta[context.FILES] = config_path or []
    ctx.meta[context.WRITE_TO] = write_to
    ctx.meta[terminal.PLAIN] = plain
    ctx.meta[terminal.NO_COLOR] = no_color or plain
    terminal.configure_logging(verbose, colour=not (no_color or plain))


def main() -> None:
    """Console-script entry point.

    A broken setup (a `SetupError`, or bad CLI usage) and a crash both exit 1, so
    they are seen. `analyze` keeps invariant #3 itself: past setup, it exits 0
    whatever happens.
    """
    # NullHighlighter, not highlighter=None: rich falls back to ReprHighlighter on None.
    # force: replace whatever handler a host process installed, so errors look the same.
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
        # typer/click exit through SystemExit: 0 for success, non-zero for bad usage,
        # which is a broken setup like any other.
        raise SystemExit(0 if exc.code in (0, None) else 1) from None
    except Exception as exc:  # noqa: BLE001 - a crash is one log line and exit 1, not a traceback
        log.error(f"internal error: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from None
