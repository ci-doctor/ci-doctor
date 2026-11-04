"""`ci-doctor matchers search`."""

from collections.abc import Iterator

import typer

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import say, table
from ci_doctor.matchers.models import Index
from ci_doctor.matchers.registry import Resolved, index_for, session

NAME = "search"


def run(
    term: str = typer.Argument(..., help="Case-insensitive text to find."),
    registry: str | None = typer.Option(None, "--registry", help="Search only this registry."),
) -> None:
    """Search matcher and bundle names, descriptions and classifications."""
    cfg = editable().matchers
    needle = term.lower()
    aliases = [registry] if registry else sorted(cfg.registries)
    seen = Resolved()
    with session() as http:
        rows = [row for alias in aliases for row in _hits(alias, index_for(cfg, alias, http, seen), needle)]
    if not rows:
        say(f"no matchers match {term!r}", "yellow")
        return
    table(["Name", "Kind", "Classification", "Description"], rows, _line)


def _hits(alias: str, served: Index, needle: str) -> Iterator[tuple[str, str, str, str]]:
    """The matchers and bundles of one registry that match the search.

    Args:
        alias: The registry's alias, prefixed to each name.
        served: Its index.
        needle: The lowercased search term.

    Yields:
        `(alias/name, kind, tags, description)` for each entry whose name,
        description or classification contains `needle`.
    """
    for kind, entries in (("matcher", served.matchers), ("bundle", served.bundles)):
        for name, e in entries.items():
            if needle in " ".join([name, e.description, *e.classification]).lower():
                yield f"{alias}/{name}", kind, ", ".join(e.classification), e.description


def _line(name: str, kind: str, tags: str, description: str) -> str:
    return f"{name}  ({kind}){f' [{tags}]' if tags else ''}  {description}"
