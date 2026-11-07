"""`ci-doctor matchers registry list`."""

from dataclasses import dataclass

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import Row, table
from ci_doctor.matchers.registry import redact

NAME = "list"


@dataclass(frozen=True)
class _Registry(Row):
    registry: str
    location: str

    def line(self) -> str:
        return f"{self.registry}  {self.location}"


def run() -> None:
    """List every configured registry."""
    registries = editable().matchers.registries
    table(_Registry, [_Registry(alias, redact(location)) for alias, location in registries.items()])
