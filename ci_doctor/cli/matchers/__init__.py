"""`ci-doctor matchers …`: the registry author's commands and the user's `matchers:` editor.

Every command exits 1 on failure, a crash included, so a registry's CI step goes red on
a stale index and a user typo never silently edits the config. Commands that change the
config touch only its `matchers:` block (`config/edit.py`) and fetch before they write,
so a bad registry or name leaves the file as it was.
"""

from collections.abc import Sequence

import typer

from ci_doctor.cli._lib.command import Command, register
from ci_doctor.cli.matchers.add import AddCommand
from ci_doctor.cli.matchers.index import IndexCommand
from ci_doctor.cli.matchers.info import InfoCommand
from ci_doctor.cli.matchers.list import ListCommand
from ci_doctor.cli.matchers.registry import app as registry_app
from ci_doctor.cli.matchers.remove import RemoveCommand
from ci_doctor.cli.matchers.search import SearchCommand
from ci_doctor.cli.matchers.verify import VerifyCommand

COMMANDS: Sequence[Command] = [
    IndexCommand(),
    VerifyCommand(),
    AddCommand(),
    RemoveCommand(),
    ListCommand(),
    SearchCommand(),
    InfoCommand(),
]

app = typer.Typer(no_args_is_help=True, help="Browse, select and build matchers.")
register(app, COMMANDS)
app.add_typer(registry_app, name="registry")
