"""`ci-doctor matchers registry …`: the registries matchers come from."""

import typer

from ci_doctor.cli.matchers.registry.add import AddCommand
from ci_doctor.cli.matchers.registry.list import ListCommand
from ci_doctor.cli.matchers.registry.ping import PingCommand
from ci_doctor.cli.matchers.registry.remove import RemoveCommand

app = typer.Typer(no_args_is_help=True, help="Manage the registries matchers come from.")
for command in (AddCommand, RemoveCommand, ListCommand, PingCommand):
    command.register(app)
