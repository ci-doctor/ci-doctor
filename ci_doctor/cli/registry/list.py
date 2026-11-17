"""`ci-doctor registry list`."""

from dataclasses import dataclass

from typing_extensions import override

from ci_doctor.cli._console.output.json_output import JSON_OPTION, emit
from ci_doctor.cli._console.output.table import Row, table
from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.registry.resolve import redact


class ListCommand(Command):
    """List every configured registry."""

    name = "list"
    examples = (
        "ci-doctor registry list",
        "ci-doctor registry list --json",
    )

    @dataclass(frozen=True)
    class _Registry(Row):
        registry: str
        location: str

        def line(self) -> str:
            return f"{self.registry}  {self.location}"

    @override
    def run(self, json_output: bool = JSON_OPTION) -> None:
        rows = [
            self._Registry(alias, redact(location))
            for alias, location in editable().matchers.registries.items()
        ]
        if json_output:
            emit(rows)
        else:
            table(self._Registry, rows)
