"""`ci-doctor matchers mirror`."""

from pathlib import Path
from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.matchers.mirror import mirror


class MirrorCommand(Command):
    """Copy a whole registry into a folder, hash-checked, to serve or to use as a file:// registry."""

    name = "mirror"

    @override
    def run(
        self,
        registry: Annotated[str, typer.Argument(metavar="NAME", help="The registry to copy, e.g. official.")],
        dest: Annotated[
            Path,
            typer.Argument(
                metavar="DIR", help="An empty or missing folder, or an earlier mirror to refresh."
            ),
        ],
    ) -> None:
        written = mirror(editable().matchers, registry, dest)
        done(f"mirrored {written} matchers and bundles from {registry} into {dest}")
