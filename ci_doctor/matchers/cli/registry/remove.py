"""`ci-doctor matchers registry remove`."""

from typing import Annotated

import typer

from ci_doctor.errors import SetupError
from ci_doctor.matchers.cli._common import done, target
from ci_doctor.matchers.config_edit import edit_matchers, read_block
from ci_doctor.matchers.models import Ref

NAME = "remove"


def run(
    names: Annotated[list[str], typer.Argument(metavar="NAME...", help="Registries to drop.")],
) -> None:
    """Remove registries from the config, unless `include` still uses them.

    Removing an override of a shipped registry (`official`) falls back to its default.
    """
    config = target()
    block = read_block(config)
    own, include = block.get("registries", {}), block.get("include", [])
    for name in names:
        if name not in own:
            raise SetupError(f"registry {name!r} is not in {config}")
        if used := [r for r in include if Ref.parse(r).alias == name]:
            raise SetupError(f"registry {name!r} is still used by matchers.include: {', '.join(used)}")

    def drop(m: dict) -> None:
        for name in names:
            m["registries"].pop(name, None)
        if not m["registries"]:
            del m["registries"]

    edit_matchers(config, drop)
    for name in dict.fromkeys(names):
        done(f"removed {name}")
