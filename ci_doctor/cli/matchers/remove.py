"""`ci-doctor matchers remove`."""

from typing import Annotated

import typer

from ci_doctor.cli._context import editable, target
from ci_doctor.cli._output import done
from ci_doctor.errors import SetupError
from ci_doctor.matchers.config_edit import edit_matchers, read_block
from ci_doctor.matchers.registry import session, via

NAME = "remove"


def run(
    refs: Annotated[list[str], typer.Argument(metavar="ALIAS/NAME...", help="Entries to drop from include.")],
) -> None:
    """Remove matchers or bundles from `matchers.include`."""
    cfg, config = editable().matchers, target()
    current = list(read_block(config).get("include", []))
    if missing := [r for r in refs if r not in current]:
        with session(cfg) as http:
            brought_by = via(cfg, current, http)
        for ref in missing:
            if (bundle := brought_by.get(ref, "direct")) != "direct":
                raise SetupError(
                    f"{ref} is not in include itself, it comes from {bundle}: remove that instead"
                )
        raise SetupError(f"{missing[0]} is not in matchers.include")
    kept = [r for r in current if r not in refs]
    edit_matchers(config, lambda m: m.__setitem__("include", kept))
    for ref in dict.fromkeys(refs):
        done(f"removed {ref}")
