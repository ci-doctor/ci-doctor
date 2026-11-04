"""How every command prints: rich by default, bare text under the global `--plain`.

Pretty output is tables, and a mark on each change. `--plain` prints one bare line
per row for scripts and `grep`, and implies `--no-color`.
"""

import logging
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence

import click
import typer
from rich import box
from rich.console import Console
from rich.pager import Pager
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

#: `ctx.meta` keys the root callback sets.
PLAIN = "ci_doctor.plain"
NO_COLOR = "ci_doctor.no_color"

Cell = str | Text

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


def say(text: str, style: str = "") -> None:
    """Print one line, styled unless `--plain`.

    Args:
        text: The line; never read as rich markup.
        style: A rich style for the pretty output.
    """
    if plain():
        typer.echo(text)
    else:
        console().print(Text(text, style=style))


def done(text: str) -> None:
    """Report a change made.

    Args:
        text: What changed.
    """
    say(text if plain() else f"✓ {text}", "green")


def skipped(text: str) -> None:
    """Report something left as it was.

    Args:
        text: What, and why.
    """
    say(text if plain() else f"· {text}", "dim")


def table(
    columns: Sequence[str], rows: Iterable[Sequence[Cell]], line: Callable[..., str], *, header: bool = True
) -> None:
    """Print rows as a table, or under `--plain` as one `line(*cells)` each.

    Args:
        columns: The header.
        rows: The cells; a `Text` keeps its style in the table.
        line: Formats one row's plain-text cells for `--plain`.
        header: Show the column names; off for a key-value listing.
    """
    if plain():
        for row in rows:
            typer.echo(line(*map(str, row)))  # str(Text) is its plain text
        return
    out = Table(*columns, box=box.SIMPLE_HEAD if header else None, header_style="bold", show_header=header)
    for row in rows:
        out.add_row(*(Text(c) if isinstance(c, str) else c for c in row))
    console().print(out)


class LessPager(Pager):
    """`$PAGER`, else `less`, with less told to pass the colour escapes through.

    Not rich's own pager: it defers to pydoc, which prefers `$MANPAGER`, and a
    MANPAGER like `col -bx | bat` strips the escape bytes and prints the rest.
    """

    def show(self, content: str) -> None:
        """Page `content`, or print it when the pager isn't installed.

        Args:
            content: The rendered text, escapes included.
        """
        cmd = shlex.split(os.environ.get("PAGER") or "less")
        if not cmd or not shutil.which(cmd[0]):
            sys.stdout.write(content)
            return
        env = {**os.environ, "LESS": f"{os.environ.get('LESS', '')} -R".strip()}
        subprocess.run(cmd, input=content, text=True, env=env, check=False)  # noqa: S603 - the user's own $PAGER
