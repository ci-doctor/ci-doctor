"""`ci-doctor config`."""

from typing import Annotated

import typer
from typing_extensions import override

from ci_doctor.cli._console.output.pager import page
from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import config_files
from ci_doctor.config.dump import as_yaml, diff_lines, schema_json
from ci_doctor.config.loader import default_config, load_config
from ci_doctor.service.setup import validate as validate_setup


class ConfigCommand(Command):
    """Show the effective config after every layer is merged."""

    name = "config"
    examples = (
        "ci-doctor config --diff   # only what your files and environment change",
        "ci-doctor config --validate   # the setup check analyze runs first",
        "ci-doctor config --schema > ci-doctor.schema.json",
    )

    @override
    def run(
        self,
        diff: Annotated[
            bool,
            typer.Option("--diff", help="Show only what your config changes against the shipped defaults."),
        ] = False,
        schema: Annotated[
            bool, typer.Option("--schema", help="Print the JSON Schema for .ci-doctor.yml and exit.")
        ] = False,
        validate: Annotated[
            bool,
            typer.Option("--validate", help="Load and validate the merged config, then report what failed."),
        ] = False,
        less: Annotated[
            bool, typer.Option("--less", help="Force the scrollable pager, even when piped.")
        ] = False,
    ) -> None:
        if validate:
            typer.echo(validate_setup(config_files()))
        elif schema:
            page(schema_json(), "json", force=less)
        elif diff:
            lines = diff_lines(as_yaml(default_config()), as_yaml(load_config(config_files())))
            page("\n".join(lines) or "no differences: running on the shipped defaults.", "diff", force=less)
        else:
            page(as_yaml(load_config(config_files())), "yaml", force=less)
