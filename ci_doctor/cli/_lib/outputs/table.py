"""Rows of output: a table, or under `--plain` one bare line per row."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import TypeVar

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from ci_doctor.cli._lib.terminal import console, plain

#: A cell: plain text, or a `Text` that keeps its style in the table.
Cell = str | Text


@dataclass(frozen=True)
class Row(ABC):
    """One table row, subclassed as a frozen dataclass: its fields are the columns, in order.

    The header is each field name, capitalised (`classification` -> Classification).
    """

    @abstractmethod
    def line(self) -> str:
        """The row under `--plain`: one bare line for scripts and `grep`."""


R = TypeVar("R", bound=Row)


def table(kind: type[R], rows: Iterable[R], *, header: bool = True) -> None:
    """Print rows as a table, or under `--plain` as one `row.line()` each.

    Args:
        kind: The row type; its fields name the columns.
        rows: The rows; a `Text` cell keeps its style in the table.
        header: Show the column names; off for a key-value listing.
    """
    if plain():
        for row in rows:
            typer.echo(row.line())
        return
    columns = [f.name for f in fields(kind)]
    out = Table(
        *(c.replace("_", " ").capitalize() for c in columns),
        box=box.SIMPLE_HEAD if header else None,
        header_style="bold",
        show_header=header,
    )
    for row in rows:
        out.add_row(*(Text(c) if isinstance(c, str) else c for c in (getattr(row, n) for n in columns)))
    console().print(out)
