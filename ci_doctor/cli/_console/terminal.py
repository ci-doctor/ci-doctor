"""The global `--plain` (implies `--no-color`) and `--no-color`, and the log console."""

import logging
import os
from collections.abc import Callable
from contextvars import ContextVar
from enum import StrEnum

from rich.console import Console
from rich.style import Style
from rich.theme import Theme

# Ours, not click's context: typer 0.26+ runs on its own copy of click (see cli/_lib/context.py).
_PLAIN: ContextVar[bool] = ContextVar("ci_doctor_plain", default=False)
_NO_COLOR: ContextVar[bool] = ContextVar("ci_doctor_no_color", default=False)

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


def use(*, plain: bool, no_color: bool) -> Callable[[], None]:
    """Record the global `--plain` and `--no-color` for one invocation; the root callback calls it.

    Args:
        plain: `--plain`, which implies `no_color`.
        no_color: `--no-color`.

    Returns:
        What undoes it, for when the invocation ends.
    """
    tokens = _PLAIN.set(plain), _NO_COLOR.set(no_color or plain)

    def undo() -> None:
        _PLAIN.reset(tokens[0])
        _NO_COLOR.reset(tokens[1])

    return undo


def plain() -> bool:
    """Whether `--plain` was passed.

    Returns:
        True for bare-text output.
    """
    return _PLAIN.get()


def no_color() -> bool:
    """Whether colour is off (`--no-color` or `--plain`).

    Returns:
        True for uncoloured output.
    """
    return _NO_COLOR.get()


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
