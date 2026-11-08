"""The terminal every command prints to: the global `--plain` and `--no-color`, and the logs.

`--plain` prints one bare line per row for scripts and `grep`, and implies `--no-color`.
"""

import logging
import os

import click
from rich.console import Console
from rich.style import Style
from rich.theme import Theme

#: `ctx.meta` keys the root callback sets.
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
#: Where the logs go: stderr, because stdout carries the report or the JSON meant
#: to be piped onward. The root callback turns its colour off for --plain/--no-color.
LOG_CONSOLE = Console(theme=_LEVEL_COLORS, stderr=True)


def _flag(key: str) -> bool:
    ctx = click.get_current_context(silent=True)
    return bool(ctx and ctx.meta.get(key))


def plain() -> bool:
    """Whether the global `--plain` was passed.

    Returns:
        True for bare-text output.
    """
    return _flag(PLAIN)


def no_color() -> bool:
    """Whether colour is off (`--no-color`, or `--plain`).

    Returns:
        True for uncoloured output.
    """
    return _flag(NO_COLOR)


def console(*, soft_wrap: bool = False) -> Console:
    """A stdout console honouring `--no-color`; no highlighting, which colours data at random.

    Args:
        soft_wrap: Never fold long lines (the config's regexes).

    Returns:
        The console.
    """
    return Console(highlight=False, no_color=no_color(), soft_wrap=soft_wrap)


def configure_logging(verbose: bool, *, colour: bool = True) -> None:
    """`--verbose` (or CI_DOCTOR_LOG_LEVEL=DEBUG) turns on debug logs across ci_doctor.*.

    Args:
        verbose: Whether `--verbose` was passed.
        colour: Colour the level names; off for `--plain` and `--no-color`.
    """
    LOG_CONSOLE.no_color = not colour or "NO_COLOR" in os.environ
    level_name = os.environ.get("CI_DOCTOR_LOG_LEVEL", "").upper()
    level = logging.DEBUG if verbose else getattr(logging, level_name, logging.INFO)
    logging.getLogger("ci_doctor").setLevel(level)
    if verbose:
        logging.getLogger("ci_doctor.cli").debug("verbose logging enabled")
