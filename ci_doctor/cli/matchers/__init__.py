"""`ci-doctor matchers …`."""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.matchers.add import AddCommand
from ci_doctor.cli.matchers.index import IndexCommand
from ci_doctor.cli.matchers.info import InfoCommand
from ci_doctor.cli.matchers.list import ListCommand
from ci_doctor.cli.matchers.registry import app as registry_app
from ci_doctor.cli.matchers.remove import RemoveCommand
from ci_doctor.cli.matchers.schema import SchemaCommand
from ci_doctor.cli.matchers.search import SearchCommand
from ci_doctor.cli.matchers.try_ import TryCommand
from ci_doctor.cli.matchers.verify import VerifyCommand

COMMANDS: Sequence[Command] = [
    IndexCommand(),
    VerifyCommand(),
    TryCommand(),
    SchemaCommand(),
    AddCommand(),
    RemoveCommand(),
    ListCommand(),
    SearchCommand(),
    InfoCommand(),
]

app = typer.Typer(no_args_is_help=True, help="Browse, select and build matchers.")
register(app, COMMANDS)
app.add_typer(registry_app, name="registry")
