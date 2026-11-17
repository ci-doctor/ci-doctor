"""`ci-doctor registry ping`."""

import time
from dataclasses import dataclass
from typing import Annotated

import httpx
import typer
from rich.text import Text
from typing_extensions import override

from ci_doctor.cli._console.output.json_output import JSON_OPTION, emit
from ci_doctor.cli._console.output.table import Cell, Row, table
from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry.resolve import Resolved, index_for, session, unknown_registry


class PingCommand(Command):
    """Fetch each registry's index and report reachability, latency and size."""

    name = "ping"
    examples = (
        "ci-doctor registry ping",
        "ci-doctor registry ping official --json",
    )

    @dataclass(frozen=True)
    class _Result:
        registry: str
        ok: bool
        latency_ms: int | None = None
        matchers: int | None = None
        bundles: int | None = None
        error: str | None = None

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
        json_output: bool = JSON_OPTION,
    ) -> None:
        cfg = editable().matchers
        if unknown := [n for n in names or [] if n not in cfg.registries]:
            raise unknown_registry(unknown[0], cfg.registries)
        with session(cfg) as http:
            results = [self._ping(cfg, alias, http) for alias in names or sorted(cfg.registries)]
        if json_output:
            emit(results)
        else:
            table(self._Ping, [self._row(r) for r in results])
        if down := sum(1 for r in results if not r.ok):
            raise SetupError(f"{down} registr{'y' if down == 1 else 'ies'} unreachable or invalid")

    def _ping(self, cfg: MatchersConfig, alias: str, http: httpx.Client) -> "PingCommand._Result":
        """Fetch one registry's index, timing it.

        Args:
            cfg: The `matchers:` block.
            alias: The registry.
            http: The command's HTTP client.

        Returns:
            Its counts and latency, or the error.
        """
        start = time.perf_counter()
        try:
            served = index_for(cfg, alias, http, Resolved())
        except SetupError as err:
            return self._Result(alias, ok=False, error=str(err))
        ms = round((time.perf_counter() - start) * 1000)
        return self._Result(
            alias, ok=True, latency_ms=ms, matchers=len(served.matchers), bundles=len(served.bundles)
        )

    def _row(self, r: "PingCommand._Result") -> "PingCommand._Ping":
        """One result as a table row."""
        if not r.ok:
            return self._Ping(r.registry, Text("FAILED", "bold red"), error=Text(r.error or "", "red"))
        return self._Ping(
            r.registry, Text("ok", "green"), f"{r.latency_ms} ms", str(r.matchers), str(r.bundles)
        )
