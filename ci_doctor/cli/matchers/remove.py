"""`ci-doctor matchers remove`."""

from typing import Annotated

import typer

from ci_doctor.cli._context import edit, editable, read
from ci_doctor.cli._output import done
from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import session, via

NAME = "remove"


def run(
    refs: Annotated[list[str], typer.Argument(metavar="ALIAS/NAME...", help="Entries to drop from include.")],
) -> None:
    """Remove matchers or bundles from `matchers.include`."""
    cfg, current = editable().matchers, list(read("matchers").get("include", []))
    if missing := [r for r in refs if r not in current]:
        raise _not_included(missing[0], cfg, current)
    kept = [r for r in current if r not in refs]
    edit("matchers", lambda m: m.__setitem__("include", kept))
    for ref in dict.fromkeys(refs):
        done(f"removed {ref}")


def _not_included(ref: str, cfg: MatchersConfig, include: list[str]) -> SetupError:
    """The error for removing a ref the file does not list, naming the bundle that brings it.

    Args:
        ref: The ref as typed.
        cfg: The `matchers:` block, to expand `include` with.
        include: The file's own `matchers.include`.

    Returns:
        The error, for the caller to raise.
    """
    with session(cfg) as http:
        bundle = via(cfg, include, http).get(ref, "direct")
    if bundle != "direct":
        return SetupError(f"{ref} is not in include itself, it comes from {bundle}: remove that instead")
    return SetupError(f"{ref} is not in matchers.include")
