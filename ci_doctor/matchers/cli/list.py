"""`ci-doctor matchers list`."""

from pathlib import Path

import typer

from ci_doctor.matchers.cli._common import CONFIG, effective
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import resolve, session

NAME = "list"


def run(config: Path = CONFIG) -> None:
    """List what `matchers.include` resolves to, and how each matcher got in."""
    cfg = effective(config)
    if not cfg.include:
        typer.echo("nothing included. Run `ci-doctor matchers add <registry>/<name>`.")
        return
    with session() as http:
        resolved = resolve(cfg, http)
    for key, via in resolved.matchers.items():
        ref = Ref.parse(key)
        tags = ", ".join(resolved.indexes[ref.alias].matchers[ref.name].classification)
        typer.echo(f"{key}  via {via}  [{tags}]")
