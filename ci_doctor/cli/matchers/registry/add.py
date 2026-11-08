"""`ci-doctor matchers registry add`."""

from pathlib import Path
from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import edit, editable
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.config.loader import anchor_registry
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import fetch_index, redact, registry_dir, session


class AddCommand(Command):
    """Add or override registries, after checking each serves a valid index.yaml.

    A relative path is stored absolute, resolved against the cwd where it was typed:
    the config it lands in (by default the user's) usually lives elsewhere.
    """

    name = "add"

    @override
    def run(
        self,
        pairs: Annotated[
            list[str], typer.Argument(metavar="NAME=LOCATION...", help="Registry alias and its folder.")
        ],
    ) -> None:
        cfg = editable().matchers
        new = dict(self._split(pair) for pair in pairs)
        with session(cfg) as http:
            for name, location in new.items():
                fetch_index(name, registry_dir(name, location), http)
        edit("matchers", lambda m: m.setdefault("registries", {}).update(new))
        for name, location in new.items():
            old = cfg.registries.get(name)
            done(
                f"{name}: {redact(old)} -> {redact(location)}" if old else f"{name}: added {redact(location)}"
            )

    @staticmethod
    def _split(pair: str) -> tuple[str, str]:
        name, eq, location = pair.partition("=")
        if not eq or not name or not location:
            raise SetupError(f"{pair!r} is not NAME=LOCATION")
        return name, anchor_registry(location, Path.cwd())
