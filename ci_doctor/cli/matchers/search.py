"""`ci-doctor matchers search`."""

from collections.abc import Iterator
from dataclasses import dataclass

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Row, table
from ci_doctor.matchers.cache import Cache
from ci_doctor.matchers.models import Index
from ci_doctor.matchers.registry import Resolved, index_for, session


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
        cfg = editable().matchers
        needle = term.lower()
        aliases = [registry] if registry else sorted(cfg.registries)
        seen = Resolved(cache=Cache.open(cfg))
        with session(cfg) as http:
            rows = [
                hit
                for alias in aliases
                for hit in self._hits(alias, index_for(cfg, alias, http, seen), needle)
            ]
        if not rows:
            say(f"no matchers match {term!r}", "yellow")
            return
        table(self._Hit, rows)

    def _hits(self, alias: str, served: Index, needle: str) -> Iterator["SearchCommand._Hit"]:
        """The matchers and bundles of one registry that match the search.

        Args:
            alias: The registry's alias, prefixed to each name.
            served: Its index.
            needle: The lowercased search term.

        Yields:
            A row for each entry whose name, description or tags contain `needle`.
        """
        for kind, entries in (("matcher", served.matchers), ("bundle", served.bundles)):
            for name, e in entries.items():
                if needle in " ".join([name, e.description, *e.tags]).lower():
                    yield self._Hit(f"{alias}/{name}", kind, ", ".join(e.tags), e.description)
