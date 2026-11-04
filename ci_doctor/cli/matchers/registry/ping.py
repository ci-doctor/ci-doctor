"""`ci-doctor matchers registry ping`."""

import time
from typing import Annotated

import typer
from rich.text import Text

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import table
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import Resolved, index_for, session, unknown_registry

NAME = "ping"


def run(
    names: Annotated[
        list[str] | None, typer.Argument(metavar="[NAME...]", help="Registries to check; all when omitted.")
    ] = None,
) -> None:
    """Fetch each registry's index and report reachability, latency and size."""
    cfg = editable().matchers
    if unknown := [n for n in names or [] if n not in cfg.registries]:
        raise unknown_registry(unknown[0], cfg.registries)
    rows = []
    with session(cfg) as http:
        for alias in names or sorted(cfg.registries):
            start = time.perf_counter()
            try:
                served = index_for(cfg, alias, http, Resolved())
            except SetupError as err:
                rows.append((alias, Text("FAILED", "bold red"), "", "", "", Text(str(err), "red")))
                continue
            ms = (time.perf_counter() - start) * 1000
            rows.append(
                (
                    alias,
                    Text("ok", "green"),
                    f"{ms:.0f} ms",
                    str(len(served.matchers)),
                    str(len(served.bundles)),
                    "",
                )
            )
    table(["Registry", "Status", "Latency", "Matchers", "Bundles", "Error"], rows, _line)
    if down := sum(1 for row in rows if row[-1]):
        raise SetupError(f"{down} registr{'y' if down == 1 else 'ies'} unreachable or invalid")


def _line(alias: str, status: str, latency: str, matchers: str, bundles: str, error: str) -> str:
    if error:
        return f"{alias}: {status} {error}"
    return f"{alias}: {status}, {latency}, {matchers} matchers, {bundles} bundles"
