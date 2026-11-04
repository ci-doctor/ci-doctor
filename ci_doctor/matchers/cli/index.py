"""`ci-doctor matchers index`."""

from pathlib import Path

import typer

from ci_doctor.matchers.cli._common import ROOT
from ci_doctor.matchers.index import write_index
from ci_doctor.matchers.models import INDEX

NAME = "index"


def run(
    root: Path = ROOT,
    check: bool = typer.Option(False, "--check", help="Write nothing; exit 1 if index.yaml is not current."),
) -> None:
    """Validate every matcher and bundle, then write index.yaml."""
    n = write_index(root, check=check)
    typer.echo(f"{root / INDEX}: {'current' if check else 'written'}, {n} entries")
