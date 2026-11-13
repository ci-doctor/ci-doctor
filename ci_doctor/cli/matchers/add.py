"""`ci-doctor matchers add`."""

from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import files
from ci_doctor.cli._lib.outputs.message import done, skipped
from ci_doctor.service.matchers import add


class AddCommand(Command):
    """Add matchers or bundles to `matchers.include`."""

    name = "add"

    @override
    def run(
        self,
        refs: Annotated[
            list[str], typer.Argument(metavar="ALIAS/NAME...", help="Matchers or bundles to include.")
        ],
    ) -> None:
        result = add(files(), refs)
        for skip in result.skipped:
            skipped(f"{skip.ref}: {skip.why}")
        for ref in result.added:
            done(f"added {ref}")
