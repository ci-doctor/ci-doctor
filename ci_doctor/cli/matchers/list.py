"""`ci-doctor matchers list`."""

from dataclasses import dataclass

from typing_extensions import override

from ci_doctor.cli._console.output.json_output import JSON_OPTION, emit
from ci_doctor.cli._console.output.message import say
from ci_doctor.cli._console.output.table import Row, table
from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.service.matchers import included


class ListCommand(Command):
    """List what `matchers.include` resolves to, how each matcher got in, and what `matchers.exclude` drops."""

    name = "list"
    examples = (
        "ci-doctor matchers list",
        "ci-doctor matchers list --json",
    )

    @dataclass(frozen=True)
    class _Included(Row):
        matcher: str
        via: str
        tags: str

        def line(self) -> str:
            return f"{self.matcher}  via {self.via}  [{self.tags}]"

    @override
    def run(self, json_output: bool = JSON_OPTION) -> None:
        cfg = editable().matchers
        if json_output:
            emit({"included": included(cfg), "excluded": cfg.exclude})
            return
        if not cfg.include:
            say("nothing included. Run `ci-doctor matchers add <registry>/<name>`.", "yellow")
            return
        table(self._Included, [self._Included(i.matcher, i.via, ", ".join(i.tags)) for i in included(cfg)])
        if cfg.exclude:
            say("excluded outputs:", "bold")
            for ref in cfg.exclude:
                say(f"  {ref}")
