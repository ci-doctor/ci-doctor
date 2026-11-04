"""ci-doctor's command line: one module per command, one package per command group.

The tree mirrors the commands: `cli/analyze.py` is `ci-doctor analyze`,
`cli/matchers/registry/ping.py` is `ci-doctor matchers registry ping`. A group's
`__init__.py` holds its typer app and lists its commands; a command is a module
with a `NAME` and a `run()`. Nothing here does the work: a command parses,
calls `service/` (or the package that owns it) and prints.

Invariant #3: the analyzer must never change a pipeline's outcome, so `main()`
catches everything and exits 0. The one exception is a broken setup
(:class:`~ci_doctor.errors.SetupError`), which exits 1.
"""

import logging
from pathlib import Path

import typer
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler

from ci_doctor import __version__
from ci_doctor.cli import _output, analyze, config
from ci_doctor.cli.matchers import app as matchers_app
from ci_doctor.errors import SetupError

log = logging.getLogger("ci_doctor.cli")
app = typer.Typer(add_completion=False, help="Explain why a CI pipeline failed (read-only postmortem).")
for command in (analyze, config):
    app.command(command.NAME)(command.run)
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
    ctx.obj = config_path or []  # the `-f` files, lowest precedence first
    ctx.meta[_output.PLAIN] = plain
    ctx.meta[_output.NO_COLOR] = no_color or plain
    _output.configure_logging(verbose, colour=not (no_color or plain))


def main() -> None:
    """Console-script entry point.

    Invariant #3: a broken setup (a `SetupError`, or bad CLI usage) exits 1, so it
    is seen; everything else, a crash included, exits 0, so the analyzer can never
    turn a passing pipeline red. The `matchers` commands raise every crash as a
    `SetupError` themselves, since they never analyze anything.
    """
    # NullHighlighter, not highlighter=None: rich falls back to ReprHighlighter on None.
    # force: replace whatever handler a host process installed, so errors look the same.
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        force=True,
        handlers=[
            RichHandler(
                console=_output.LOG_CONSOLE,
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
    except BaseException as exc:  # noqa: BLE001 - analyzer must never alter pipeline outcome
        log.error(f"internal error, exiting 0 to preserve pipeline status: {exc}")
        raise SystemExit(0) from None
