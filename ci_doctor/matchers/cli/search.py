"""`ci-doctor matchers search`."""

from collections.abc import Iterator
from pathlib import Path

import typer

from ci_doctor.matchers.cli._common import CONFIG, effective
from ci_doctor.matchers.models import Index
from ci_doctor.matchers.registry import Resolved, index_for, session

NAME = "search"


def run(
    term: str = typer.Argument(..., help="Case-insensitive text to find."),
    registry: str | None = typer.Option(None, "--registry", help="Search only this registry."),
    config: Path = CONFIG,
) -> None:
    """Search matcher and bundle names, descriptions and classifications."""
    cfg = effective(config)
    needle = term.lower()
    aliases = [registry] if registry else sorted(cfg.registries)
    seen = Resolved()
    hits = 0
    with session() as http:
        for alias in aliases:
            for line in _search_index(alias, index_for(cfg, alias, http, seen), needle):
                typer.echo(line)
                hits += 1
    if not hits:
        typer.echo(f"no matchers match {term!r}")


def _search_index(alias: str, served: Index, needle: str) -> Iterator[str]:
    """The `search` output lines for one registry's index.

    Args:
        alias: The registry's alias, prefixed to each name.
        served: Its index.
        needle: The lowercased search term.

    Yields:
        One line per matcher or bundle whose name, description or classification
        contains `needle`.
    """
    for kind, entries in (("matcher", served.matchers), ("bundle", served.bundles)):
        for name, e in entries.items():
            if needle in " ".join([name, e.description, *e.classification]).lower():
                tags = f" [{', '.join(e.classification)}]" if e.classification else ""
                yield f"{alias}/{name}  ({kind}){tags}  {e.description}"
