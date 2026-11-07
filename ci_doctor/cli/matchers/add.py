"""`ci-doctor matchers add`."""

from typing import Annotated

import typer

from ci_doctor.cli._context import edit, editable, read
from ci_doctor.cli._output import done, skipped
from ci_doctor.matchers.registry import Resolved, check_ref, session, via

NAME = "add"


def run(
    refs: Annotated[
        list[str], typer.Argument(metavar="ALIAS/NAME...", help="Matchers or bundles to include.")
    ],
) -> None:
    """Add matchers or bundles to `matchers.include`."""
    cfg = editable().matchers
    current = list(read("matchers").get("include", []))
    seen = Resolved()
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
