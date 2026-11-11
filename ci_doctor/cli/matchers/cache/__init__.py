"""`ci-doctor matchers cache …`: the local copy of registry files."""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.matchers.cache.clean import CleanCommand
from ci_doctor.cli.matchers.cache.info import InfoCommand
from ci_doctor.cli.matchers.cache.prune import PruneCommand

COMMANDS: Sequence[Command] = [InfoCommand(), PruneCommand(), CleanCommand()]

app = typer.Typer(no_args_is_help=True, help="Inspect and empty the local copy of registry files.")
register(app, COMMANDS)
