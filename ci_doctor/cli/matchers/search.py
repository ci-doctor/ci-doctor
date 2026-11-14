"""`ci-doctor matchers search`."""

from dataclasses import dataclass

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Row, table
from ci_doctor.service.matchers import search


class SearchCommand(Command):
    """Search matcher and bundle names, descriptions and tags."""

    name = "search"

    @dataclass(frozen=True)
    class _Hit(Row):
        name: str
        kind: str
        tags: str
        description: str

        def line(self) -> str:
            tags = f" [{self.tags}]" if self.tags else ""
            return f"{self.name}  ({self.kind}){tags}  {self.description}"

    @override
    def run(
        self,
        term: str = typer.Argument(..., help="Case-insensitive text to find."),
        registry: str | None = typer.Option(None, "--registry", help="Search only this registry."),
    ) -> None:
        hits = search(editable().matchers, term, registry)
        if not hits:
            say(f"no matchers match {term!r}", "yellow")
            return
        table(self._Hit, [self._Hit(h.name, h.kind, ", ".join(h.tags), h.description) for h in hits])
