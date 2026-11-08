"""`ci-doctor matchers remove`."""

from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import edit, editable, read
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import session, via


class RemoveCommand(Command):
    """Remove matchers or bundles from `matchers.include`."""

    name = "remove"

    @override
    def run(
        self,
        refs: Annotated[
            list[str], typer.Argument(metavar="ALIAS/NAME...", help="Entries to drop from include.")
        ],
    ) -> None:
        cfg, current = editable().matchers, list(read("matchers").get("include", []))
        if missing := [r for r in refs if r not in current]:
            raise self._not_included(missing[0], cfg, current)
        kept = [r for r in current if r not in refs]
        edit("matchers", lambda m: m.__setitem__("include", kept))
        for ref in dict.fromkeys(refs):
            done(f"removed {ref}")

    @staticmethod
    def _not_included(ref: str, cfg: MatchersConfig, include: list[str]) -> SetupError:
        """The error for removing a ref the file does not list, naming the bundle that brings it."""
        with session(cfg) as http:
            bundle = via(cfg, include, http).get(ref, "direct")
        if bundle != "direct":
            return SetupError(f"{ref} is not in include itself, it comes from {bundle}: remove that instead")
        return SetupError(f"{ref} is not in matchers.include")
