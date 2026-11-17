"""`ci-doctor cache prune`."""

from typing_extensions import override

from ci_doctor.cli._console.output.message import done
from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.matchers.registry.cache import Cache, root_for


class PruneCommand(Command):
    """Delete cached files that no cached index lists any more: old versions of changed matchers."""

    name = "prune"
    examples = ("ci-doctor cache prune   # drop files no cached index lists",)

    @override
    def run(self) -> None:
        root = root_for(editable().matchers)
        dropped = Cache(root).prune() if root.is_dir() else []
        done(f"removed {len(dropped)} file{'' if len(dropped) == 1 else 's'} no cached index lists")
