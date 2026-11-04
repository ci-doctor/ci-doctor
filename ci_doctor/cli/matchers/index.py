"""`ci-doctor matchers index`."""

from pathlib import Path

import typer

from ci_doctor.cli._output import done
from ci_doctor.matchers.index import write_index
from ci_doctor.matchers.models import INDEX

NAME = "index"


def run(
    root: Path = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml)."),
    check: bool = typer.Option(False, "--check", help="Write nothing; exit 1 if index.yaml is not current."),
) -> None:
    """Validate every matcher and bundle, then write index.yaml."""
    n = write_index(root, check=check)
    done(f"{root / INDEX}: {'current' if check else 'written'}, {n} entries")
