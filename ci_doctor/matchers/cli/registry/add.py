"""`ci-doctor matchers registry add`."""

from pathlib import Path
from typing import Annotated

import typer

from ci_doctor.config.loader import anchor_registry
from ci_doctor.errors import SetupError
from ci_doctor.matchers.cli._common import CONFIG, effective
from ci_doctor.matchers.config_edit import edit_matchers
from ci_doctor.matchers.registry import fetch_index, redact, registry_dir, session

NAME = "add"


def run(
    pairs: Annotated[
        list[str], typer.Argument(metavar="NAME=LOCATION...", help="Registry alias and its folder.")
    ],
    config: Path = CONFIG,
) -> None:
    """Add or override registries, after checking each serves a valid index.yaml."""
    cfg = effective(config)
    new: dict[str, str] = {}
    for pair in pairs:
        name, eq, location = pair.partition("=")
        if not eq or not name or not location:
            raise SetupError(f"{pair!r} is not NAME=LOCATION")
        new[name] = location
    with session() as http:
        for name, location in new.items():
            fetch_index(name, registry_dir(name, anchor_registry(location, config.parent)), http)
    edit_matchers(config, lambda m: m.setdefault("registries", {}).update(new))
    for name, location in new.items():
        old = cfg.registries.get(name)
        typer.echo(
            f"{name}: {redact(old)} -> {redact(location)}" if old else f"{name}: added {redact(location)}"
        )
