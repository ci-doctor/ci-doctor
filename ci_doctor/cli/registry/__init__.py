"""`ci-doctor registry …`: where matchers come from, and building a registry."""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.registry.add import AddCommand
from ci_doctor.cli.registry.index import IndexCommand
from ci_doctor.cli.registry.list import ListCommand
from ci_doctor.cli.registry.mirror import MirrorCommand
from ci_doctor.cli.registry.ping import PingCommand
from ci_doctor.cli.registry.remove import RemoveCommand

COMMANDS: Sequence[Command] = [
    AddCommand(),
    RemoveCommand(),
    ListCommand(),
    PingCommand(),
    IndexCommand(),
    MirrorCommand(),
]

app = typer.Typer(no_args_is_help=True, help="Manage where matchers come from, and build a registry.")
register(app, COMMANDS)
