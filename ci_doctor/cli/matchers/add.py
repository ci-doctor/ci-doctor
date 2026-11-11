"""`ci-doctor matchers add`."""

from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import edit, editable, read
from ci_doctor.cli._lib.outputs.message import done, skipped
from ci_doctor.matchers.cache import Cache
from ci_doctor.matchers.registry import Resolved, check_ref, session, via


class AddCommand(Command):
    """Add matchers or bundles to `matchers.include`."""

    name = "add"

    @override
    def run(
        self,
        refs: Annotated[
            list[str], typer.Argument(metavar="ALIAS/NAME...", help="Matchers or bundles to include.")
        ],
    ) -> None:
        cfg = editable().matchers
        current = list(read("matchers").get("include", []))
        seen = Resolved(cache=Cache.open(cfg))
        with session(cfg) as http:
            parsed = [str(check_ref(r, cfg, http, seen)) for r in refs]
            brought_by = via(cfg, current, http)
        new: list[str] = []
        for ref in parsed:
            if ref in current or ref in new:
                skipped(f"{ref}: already in include")
            elif (bundle := brought_by.get(ref, "direct")) != "direct":
                skipped(f"{ref}: already included via {bundle}")
            else:
                new.append(ref)
        if new:
            edit("matchers", lambda m: m.__setitem__("include", [*current, *new]))
        for ref in new:
            done(f"added {ref}")
