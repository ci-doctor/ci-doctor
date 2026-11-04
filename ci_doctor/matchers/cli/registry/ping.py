"""`ci-doctor matchers registry ping`."""

import time
from pathlib import Path
from typing import Annotated

import typer

from ci_doctor.errors import SetupError
from ci_doctor.matchers.cli._common import CONFIG, effective
from ci_doctor.matchers.registry import Resolved, index_for, session, unknown_registry

NAME = "ping"


def run(
    names: Annotated[
        list[str] | None, typer.Argument(metavar="[NAME...]", help="Registries to check; all when omitted.")
    ] = None,
    config: Path = CONFIG,
) -> None:
    """Fetch each registry's index and report reachability, latency and size."""
    cfg = effective(config)
    if unknown := [n for n in names or [] if n not in cfg.registries]:
        raise unknown_registry(unknown[0], cfg.registries)
    down = 0
    with session() as http:
        for alias in names or sorted(cfg.registries):
            start = time.perf_counter()
            try:
                served = index_for(cfg, alias, http, Resolved())
            except SetupError as err:
                down += 1
                typer.echo(f"{alias}: FAILED {err}")
                continue
            ms = (time.perf_counter() - start) * 1000
            typer.echo(
                f"{alias}: ok, {ms:.0f} ms, {len(served.matchers)} matchers, {len(served.bundles)} bundles"
            )
    if down:
        raise SetupError(f"{down} registr{'y' if down == 1 else 'ies'} unreachable or invalid")
