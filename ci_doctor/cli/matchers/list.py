"""`ci-doctor matchers list`."""

from dataclasses import dataclass

from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Row, table
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import resolve, session


class ListCommand(Command):
    """List what `matchers.include` resolves to, how each matcher got in, and what `matchers.exclude` drops."""

    name = "list"

    @dataclass(frozen=True)
    class _Included(Row):
        matcher: str
        via: str
        tags: str

        def line(self) -> str:
            return f"{self.matcher}  via {self.via}  [{self.tags}]"

    @override
    def run(self) -> None:
        cfg = editable().matchers
        if not cfg.include:
            say("nothing included. Run `ci-doctor matchers add <registry>/<name>`.", "yellow")
            return
        with session(cfg) as http:
            resolved = resolve(cfg, http)
        rows = []
        for key, via in resolved.matchers.items():
            ref = Ref.parse(key)
            tags = ", ".join(resolved.indexes[ref.alias].matchers[ref.name].tags)
            rows.append(self._Included(key, via, tags))
        table(self._Included, rows)
        if cfg.exclude:
            say("excluded outputs:", "bold")
            for ref in cfg.exclude:
                say(f"  {ref}")
