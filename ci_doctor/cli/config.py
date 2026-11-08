"""`ci-doctor config`: the effective config after every layer is merged."""

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import config_files
from ci_doctor.cli._lib.outputs.pager import page
from ci_doctor.config.dump import as_yaml, diff_lines, schema_json
from ci_doctor.config.loader import default_config, load_config
from ci_doctor.service.setup import validate as validate_setup


class ConfigCommand(Command):
    """Show the effective config after every layer is merged.

    Useful for answering "why is it doing that" — the answer is almost always a
    layer you forgot was applied. Pages the output on a terminal; pipe it (or pass
    the global `--plain`) to get the raw text back.
    """

    name = "config"

    @override
    def run(
        self,
        diff: bool = typer.Option(
            False, "--diff", help="Show only what your config changes against the shipped defaults."
        ),
        schema: bool = typer.Option(
            False, "--schema", help="Print the JSON Schema for .ci-doctor.yml and exit."
        ),
        validate: bool = typer.Option(
            False, "--validate", help="Load and validate the merged config, then report what failed."
        ),
        less: bool = typer.Option(False, "--less", help="Force the scrollable pager, even when piped."),
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
