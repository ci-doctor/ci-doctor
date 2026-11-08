"""`ci-doctor matchers verify`."""

from pathlib import Path

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.matchers.verify import verify


class VerifyCommand(Command):
    """Run every matcher over its fixtures and compare with the committed .expected files."""

    name = "verify"

    @override
    def run(
        self,
        root: Path = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml)."),
        update: bool = typer.Option(
            False,
            "--update",
            help="Write each fixture's .expected from the current output instead of comparing.",
        ),
    ) -> None:
        n = verify(root, update=update)
        done(f"{n} matchers verified" + (" (.expected files written; review the diff)" if update else ""))
