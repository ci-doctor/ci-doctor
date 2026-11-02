"""`ci-doctor matchers …`: the registry author's commands.

Both exit 1 on failure (a `SetupError`, invariant #3) so a registry's CI step goes
red on a stale index or a broken matcher.
"""

from pathlib import Path

import typer

from ci_doctor.matchers.registry import write_index
from ci_doctor.matchers.verify import verify as run_verify

app = typer.Typer(no_args_is_help=True, help="Build and test a matcher registry.")

_ROOT = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml).")


@app.command()
def index(
    root: Path = _ROOT,
    check: bool = typer.Option(False, "--check", help="Write nothing; exit 1 if index.yaml is not current."),
) -> None:
    """Validate every matcher and bundle, then write index.yaml."""
    n = write_index(root, check=check)
    typer.echo(f"{root / 'index.yaml'}: {'current' if check else 'written'}, {n} entries")


@app.command()
def verify(
    root: Path = _ROOT,
    update: bool = typer.Option(
        False, "--update", help="Write each fixture's .expected from the current output instead of comparing."
    ),
) -> None:
    """Run every matcher over its fixtures and compare with the committed .expected files."""
    n = run_verify(root, update=update)
    typer.echo(f"{n} matchers verified" + (" (.expected files written; review the diff)" if update else ""))
