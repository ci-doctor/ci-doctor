"""`ci-doctor matchers verify`."""

from pathlib import Path

import typer

from ci_doctor.matchers.cli._common import ROOT
from ci_doctor.matchers.verify import verify

NAME = "verify"


def run(
    root: Path = ROOT,
    update: bool = typer.Option(
        False, "--update", help="Write each fixture's .expected from the current output instead of comparing."
    ),
) -> None:
    """Run every matcher over its fixtures and compare with the committed .expected files."""
    n = verify(root, update=update)
    typer.echo(f"{n} matchers verified" + (" (.expected files written; review the diff)" if update else ""))
