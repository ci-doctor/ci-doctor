"""Rows: a table, or one bare line each under `--plain`."""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import TypeVar

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from ci_doctor.cli._lib.terminal import console, plain

Cell = str | Text


@dataclass(frozen=True)
class Row(ABC):
    """A row, subclassed as a frozen dataclass: its fields are the columns."""

    @abstractmethod
    def line(self) -> str:
        """The row under `--plain`."""


R = TypeVar("R", bound=Row)


def table(kind: type[R], rows: Iterable[R], *, header: bool = True) -> None:
    """Print rows as a table headed by `kind`'s field names, or `row.line()` each under `--plain`."""
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
