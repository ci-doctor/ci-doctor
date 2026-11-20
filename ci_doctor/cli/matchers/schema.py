"""`ci-doctor matchers schema`."""

from pathlib import Path
from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._console.output.message import done
from ci_doctor.cli._console.output.pager import page
from ci_doctor.cli._lib.command import Command
from ci_doctor.engine import SCHEMAS
from ci_doctor.errors import SetupError
from ci_doctor.matchers.schema import matcher_schema, write_schemas


class SchemaCommand(Command):
    """Print the JSON Schema of a matcher file, or write one per known version for a release."""

    name = "schema"
    examples = (
        "ci-doctor matchers schema > matcher.schema.json",
        "ci-doctor matchers schema --out schemas/",
    )

    @override
    def run(
        self,
        version: Annotated[int, typer.Option("--version", help="The matcher schema version to print.")] = max(
            SCHEMAS
        ),
        out: Annotated[
            Path | None,
            typer.Option(
                "--out", help="Write matcher-v<N>.schema.json for every known version into this directory."
            ),
        ] = None,
    ) -> None:
        if out is not None:
            for path in write_schemas(out):
                done(f"{path} written")
            return
        try:
            text = matcher_schema(version)
        except ValueError as err:
            raise SetupError(str(err)) from None
        page(text, "json", force=False)
