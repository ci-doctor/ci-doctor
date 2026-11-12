"""`ci-doctor matchers …`."""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.matchers.add import AddCommand
from ci_doctor.cli.matchers.cache import app as cache_app
from ci_doctor.cli.matchers.examine import ExamineCommand
from ci_doctor.cli.matchers.index import IndexCommand
from ci_doctor.cli.matchers.info import InfoCommand
from ci_doctor.cli.matchers.list import ListCommand
from ci_doctor.cli.matchers.mirror import MirrorCommand
from ci_doctor.cli.matchers.pull import PullCommand
from ci_doctor.cli.matchers.registry import app as registry_app
from ci_doctor.cli.matchers.remove import RemoveCommand
from ci_doctor.cli.matchers.schema import SchemaCommand
from ci_doctor.cli.matchers.search import SearchCommand
from ci_doctor.cli.matchers.verify import VerifyCommand

COMMANDS: Sequence[Command] = [
    IndexCommand(),
    VerifyCommand(),
    ExamineCommand(),
    SchemaCommand(),
    AddCommand(),
    RemoveCommand(),
    ListCommand(),
    SearchCommand(),
    InfoCommand(),
    PullCommand(),
    MirrorCommand(),
]

app = typer.Typer(no_args_is_help=True, help="Browse, select and build matchers.")
register(app, COMMANDS)
app.add_typer(registry_app, name="registry")
app.add_typer(cache_app, name="cache")
