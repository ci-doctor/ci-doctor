"""`ci-doctor matchers registry remove`."""

from typing import Annotated

import typer

from ci_doctor.cli._context import edit, read, target
from ci_doctor.cli._output import done
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import Ref

NAME = "remove"


def run(
    names: Annotated[list[str], typer.Argument(metavar="NAME...", help="Registries to drop.")],
) -> None:
    """Remove registries from the config, unless `include` still uses them.

    Removing an override of a shipped registry (`official`) falls back to its default.
    """
    block = read("matchers")
    own, include = block.get("registries", {}), block.get("include", [])
    for name in names:
        if name not in own:
            raise SetupError(f"registry {name!r} is not in {target()}")
        if used := [r for r in include if Ref.parse(r).alias == name]:
            raise SetupError(f"registry {name!r} is still used by matchers.include: {', '.join(used)}")
    kept = {k: v for k, v in own.items() if k not in names}

    def drop(m: dict) -> None:
        if kept:
            m["registries"] = kept
        else:
            del m["registries"]

    edit("matchers", drop)
    for name in dict.fromkeys(names):
        done(f"removed {name}")
