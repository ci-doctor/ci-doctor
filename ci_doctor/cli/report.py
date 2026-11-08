"""`ci-doctor report`."""

from dataclasses import dataclass

from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import config_files
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Row, table
from ci_doctor.service.report import build


class ReportCommand(Command):
    """Print what a bug report needs: versions, platform, config and environment.

    Paste the output into the issue. It holds no secrets: environment variables
    show as set or unset, and config files and endpoints are named, not shown.
    """

    name = "report"

    @dataclass(frozen=True)
    class _Field(Row):
        key: str
        value: str

        def line(self) -> str:
            return f"  {self.key}: {self.value}"

    @override
    def run(self) -> None:
        for section in build(config_files()):
            say(section.title, "bold")
            table(self._Field, [self._Field(k, v) for k, v in section.fields], header=False)
