"""`ci-doctor matchers schema`."""

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.outputs.pager import page
from ci_doctor.errors import SetupError
from ci_doctor.matchers.schema import matcher_schema


class SchemaCommand(Command):
    """Print the JSON Schema of a matcher file, for editors and tooling."""

    name = "schema"

    @override
    def run(
        self,
        version: int = typer.Option(1, "--version", help="The matcher schema version."),
    ) -> None:
        try:
            text = matcher_schema(version)
        except ValueError as err:
            raise SetupError(str(err)) from None
        page(text, "json", force=False)
