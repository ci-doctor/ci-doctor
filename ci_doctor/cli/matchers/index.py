"""`ci-doctor matchers index`."""

from pathlib import Path

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.matchers.index import write_index
from ci_doctor.matchers.models import INDEX


class IndexCommand(Command):
    """Validate every matcher and bundle, then write index.yaml."""

    name = "index"

    @override
    def run(
        self,
        root: Path = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml)."),
        check: bool = typer.Option(
            False, "--check", help="Write nothing; exit 1 if index.yaml is not current."
        ),
    ) -> None:
        n = write_index(root, check=check)
        done(f"{root / INDEX}: {'current' if check else 'written'}, {n} entries")
