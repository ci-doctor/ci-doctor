"""`ci-doctor matchers registry add`."""

from pathlib import Path
from typing import Annotated

import typer

from ci_doctor.cli._context import edit, editable
from ci_doctor.cli._output import done
from ci_doctor.config.loader import anchor_registry
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import fetch_index, redact, registry_dir, session

NAME = "add"


def run(
    pairs: Annotated[
        list[str], typer.Argument(metavar="NAME=LOCATION...", help="Registry alias and its folder.")
    ],
) -> None:
    """Add or override registries, after checking each serves a valid index.yaml.

    A relative path is stored absolute, resolved against the cwd where it was typed:
    the config it lands in (by default the user's) usually lives elsewhere.
    """
    cfg = editable().matchers
    new = dict(_split(pair) for pair in pairs)
    with session(cfg) as http:
        for name, location in new.items():
            fetch_index(name, registry_dir(name, location), http)
    edit("matchers", lambda m: m.setdefault("registries", {}).update(new))
    for name, location in new.items():
        old = cfg.registries.get(name)
        done(f"{name}: {redact(old)} -> {redact(location)}" if old else f"{name}: added {redact(location)}")


def _split(pair: str) -> tuple[str, str]:
    name, eq, location = pair.partition("=")
    if not eq or not name or not location:
        raise SetupError(f"{pair!r} is not NAME=LOCATION")
    return name, anchor_registry(location, Path.cwd())
