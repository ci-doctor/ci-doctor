"""`ci-doctor matchers registry …`: the registries matchers come from."""

import typer

from ci_doctor.matchers.cli.registry import add, list, ping, remove

app = typer.Typer(no_args_is_help=True, help="Manage the registries matchers come from.")
for command in (add, remove, list, ping):
    app.command(command.NAME)(command.run)
