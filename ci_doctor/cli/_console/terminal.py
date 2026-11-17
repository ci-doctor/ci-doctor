"""The global `--plain` (implies `--no-color`) and `--no-color`, and the log console."""

import logging
import os
from enum import StrEnum

import click
from rich.console import Console
from rich.style import Style
from rich.theme import Theme

PLAIN = "ci_doctor.plain"
NO_COLOR = "ci_doctor.no_color"

_LEVEL_COLORS = Theme(
    {
        "logging.level.debug": Style(color="blue"),
        "logging.level.info": Style(color="green"),
        "logging.level.warning": Style(color="yellow"),
        "logging.level.error": Style(color="red"),
        "logging.level.critical": Style(color="red", bold=True),
    }
)
# stderr: stdout carries the report or JSON meant to be piped.
LOG_CONSOLE = Console(theme=_LEVEL_COLORS, stderr=True)


def _flag(key: str) -> bool:
    ctx = click.get_current_context(silent=True)
    return bool(ctx and ctx.meta.get(key))


def plain() -> bool:
    """Whether `--plain` was passed.

    Returns:
        True for bare-text output.
    """
    return _flag(PLAIN)


def no_color() -> bool:
    """Whether colour is off (`--no-color` or `--plain`).

    Returns:
        True for uncoloured output.
    """
    return _flag(NO_COLOR)


def console(*, soft_wrap: bool = False) -> Console:
    """A stdout console honouring `--no-color`. No highlighting: it colours data at random.

    Args:
        soft_wrap: Never fold long lines (the config's regexes).

    Returns:
        The console.
    """
    return Console(highlight=False, no_color=no_color(), soft_wrap=soft_wrap)


class LogLevel(StrEnum):
    """What `--log-level` accepts."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def configure_logging(level: str | None, *, colour: bool = True) -> None:
    """Set ci_doctor's log level: `--log-level`, else `CI_DOCTOR_LOG_LEVEL`, else info.

    Args:
        level: The `--log-level` value, or None when it wasn't passed.
        colour: Colour the level names; off for `--plain` and `--no-color`.
    """
    LOG_CONSOLE.no_color = not colour or "NO_COLOR" in os.environ
    name = (level or os.environ.get("CI_DOCTOR_LOG_LEVEL") or "info").upper()
    logging.getLogger("ci_doctor").setLevel(logging.getLevelNamesMapping().get(name, logging.INFO))
