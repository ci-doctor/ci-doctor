"""`ci-doctor matchers search`."""

from collections.abc import Iterator
from dataclasses import dataclass

import typer

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import Row, say, table
from ci_doctor.matchers.models import Index
from ci_doctor.matchers.registry import Resolved, index_for, session

NAME = "search"


@dataclass(frozen=True)
class _Hit(Row):
    name: str
    kind: str
    classification: str
    description: str

    def line(self) -> str:
        tags = f" [{self.classification}]" if self.classification else ""
        return f"{self.name}  ({self.kind}){tags}  {self.description}"


def run(
    term: str = typer.Argument(..., help="Case-insensitive text to find."),
    registry: str | None = typer.Option(None, "--registry", help="Search only this registry."),
) -> None:
    """Search matcher and bundle names, descriptions and classifications."""
    cfg = editable().matchers
    needle = term.lower()
    aliases = [registry] if registry else sorted(cfg.registries)
    seen = Resolved()
    with session(cfg) as http:
        rows = [row for alias in aliases for row in _hits(alias, index_for(cfg, alias, http, seen), needle)]
    if not rows:
        say(f"no matchers match {term!r}", "yellow")
        return
    table(_Hit, rows)


def _hits(alias: str, served: Index, needle: str) -> Iterator[_Hit]:
    """The matchers and bundles of one registry that match the search.

    Args:
        alias: The registry's alias, prefixed to each name.
        served: Its index.
        needle: The lowercased search term.

    Yields:
        A row for each entry whose name,
        description or classification contains `needle`.
    """
    for kind, entries in (("matcher", served.matchers), ("bundle", served.bundles)):
        for name, e in entries.items():
            if needle in " ".join([name, e.description, *e.classification]).lower():
                yield _Hit(f"{alias}/{name}", kind, ", ".join(e.classification), e.description)
