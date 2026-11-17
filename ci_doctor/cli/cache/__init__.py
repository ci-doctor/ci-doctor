"""`ci-doctor cache …`: the local copy of registry files."""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.cache.clean import CleanCommand
from ci_doctor.cli.cache.info import InfoCommand
from ci_doctor.cli.cache.prune import PruneCommand
from ci_doctor.cli.cache.pull import PullCommand

COMMANDS: Sequence[Command] = [InfoCommand(), PruneCommand(), CleanCommand(), PullCommand()]

app = typer.Typer(no_args_is_help=True, help="Fill, inspect and empty the local copy of registry files.")
register(app, COMMANDS)
