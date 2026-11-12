"""`ci-doctor matchers cache clean`."""

from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import done
from ci_doctor.matchers.cache import Cache, root_for


class CleanCommand(Command):
    """Delete everything in the cache; the next run fetches every registry file again."""

    name = "clean"

    @override
    def run(self) -> None:
        root = root_for(editable().matchers)
        if root.is_dir():
            Cache(root).clean()
        done(f"emptied {root}")
