"""`ci-doctor matchers registry list`."""

from dataclasses import dataclass

from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.table import Row, table
from ci_doctor.matchers.registry import redact


class ListCommand(Command):
    """List every configured registry."""

    name = "list"

    @dataclass(frozen=True)
    class _Registry(Row):
        registry: str
        location: str

        def line(self) -> str:
            return f"{self.registry}  {self.location}"

    @override
    def run(self) -> None:
        registries = editable().matchers.registries
        table(
            self._Registry,
            [self._Registry(alias, redact(location)) for alias, location in registries.items()],
        )
