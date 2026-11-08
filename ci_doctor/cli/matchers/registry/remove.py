"""`ci-doctor matchers registry remove`."""

from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import edit, read, target
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import Ref


class RemoveCommand(Command):
    """Remove registries from the config, unless `include` still uses them."""

    name = "remove"

    @override
    def run(
        self,
        names: Annotated[list[str], typer.Argument(metavar="NAME...", help="Registries to drop.")],
    ) -> None:
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
