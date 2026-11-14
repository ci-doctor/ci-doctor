"""`ci-doctor matchers …`: choosing the matchers a project runs, and writing and testing them."""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.matchers.add import AddCommand
from ci_doctor.cli.matchers.examine import ExamineCommand
from ci_doctor.cli.matchers.info import InfoCommand
from ci_doctor.cli.matchers.list import ListCommand
from ci_doctor.cli.matchers.remove import RemoveCommand
from ci_doctor.cli.matchers.schema import SchemaCommand
from ci_doctor.cli.matchers.search import SearchCommand
from ci_doctor.cli.matchers.verify import VerifyCommand

COMMANDS: Sequence[Command] = [
    AddCommand(),
    RemoveCommand(),
    ListCommand(),
    SearchCommand(),
    InfoCommand(),
    ExamineCommand(),
    VerifyCommand(),
    SchemaCommand(),
]

app = typer.Typer(no_args_is_help=True, help="Choose the matchers a project runs, and write and test them.")
register(app, COMMANDS)
