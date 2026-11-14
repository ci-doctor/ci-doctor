"""`ci-doctor matchers remove`."""

from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import files
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.service.matchers import remove


class RemoveCommand(Command):
    """Remove matchers or bundles from `matchers.include`."""

    name = "remove"

    @override
    def run(
        self,
        refs: Annotated[
            list[str], typer.Argument(metavar="ALIAS/NAME...", help="Entries to drop from include.")
        ],
    ) -> None:
        for ref in remove(files(), refs):
            done(f"removed {ref}")
