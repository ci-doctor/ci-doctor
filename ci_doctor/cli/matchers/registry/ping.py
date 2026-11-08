"""`ci-doctor matchers registry ping`."""

import time
from dataclasses import dataclass
from typing import Annotated

import httpx
import typer
from rich.text import Text
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.table import Cell, Row, table
from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import Resolved, index_for, session, unknown_registry


class PingCommand(Command):
    """Fetch each registry's index and report reachability, latency and size."""

    name = "ping"

    @dataclass(frozen=True)
    class _Ping(Row):
        registry: str
        status: Cell
        latency: str = ""
        matchers: str = ""
        bundles: str = ""
        error: Cell = ""

        def line(self) -> str:
            if self.error:
                return f"{self.registry}: {self.status} {self.error}"
            return f"{self.registry}: {self.status}, {self.latency}, {self.matchers} matchers, {self.bundles} bundles"

    @override
    def run(
        self,
        names: Annotated[
            list[str] | None,
            typer.Argument(metavar="[NAME...]", help="Registries to check; all when omitted."),
        ] = None,
    ) -> None:
        cfg = editable().matchers
        if unknown := [n for n in names or [] if n not in cfg.registries]:
            raise unknown_registry(unknown[0], cfg.registries)
        with session(cfg) as http:
            rows = [self._ping(cfg, alias, http) for alias in names or sorted(cfg.registries)]
        table(self._Ping, rows)
        if down := sum(1 for row in rows if row.error):
            raise SetupError(f"{down} registr{'y' if down == 1 else 'ies'} unreachable or invalid")

    def _ping(self, cfg: MatchersConfig, alias: str, http: httpx.Client) -> "PingCommand._Ping":
        """Fetch one registry's index, timing it.

        Args:
            cfg: The `matchers:` block.
            alias: The registry.
            http: The command's HTTP client.

        Returns:
            Its row: the counts, or the error.
        """
        start = time.perf_counter()
        try:
            served = index_for(cfg, alias, http, Resolved())
        except SetupError as err:
            return self._Ping(alias, Text("FAILED", "bold red"), error=Text(str(err), "red"))
        ms = (time.perf_counter() - start) * 1000
        counts = str(len(served.matchers)), str(len(served.bundles))
        return self._Ping(alias, Text("ok", "green"), f"{ms:.0f} ms", *counts)
