"""`ci-doctor matchers add`."""

from typing import Annotated

import typer

from ci_doctor.cli._context import editable, target
from ci_doctor.cli._output import done, skipped
from ci_doctor.matchers.config_edit import edit_matchers, read_block
from ci_doctor.matchers.registry import Resolved, check_ref, session, via

NAME = "add"


def run(
    refs: Annotated[
        list[str], typer.Argument(metavar="ALIAS/NAME...", help="Matchers or bundles to include.")
    ],
) -> None:
    """Add matchers or bundles to `matchers.include`."""
    cfg, config = editable().matchers, target()
    current = list(read_block(config).get("include", []))
    seen = Resolved()
    new: list[str] = []
    with session() as http:
        parsed = [check_ref(r, cfg, http, seen) for r in refs]
        brought_by = via(cfg, current, http)
    for ref in map(str, parsed):
        if ref in current or ref in new:
            skipped(f"{ref}: already in include")
        elif brought_by.get(ref, "direct") != "direct":
            skipped(f"{ref}: already included via {brought_by[ref]}")
        else:
            new.append(ref)
    if new:
        edit_matchers(config, lambda m: m.__setitem__("include", [*current, *new]))
    for ref in new:
        done(f"added {ref}")
