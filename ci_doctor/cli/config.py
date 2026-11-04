"""`ci-doctor config`: the effective config after every layer is merged."""

import sys

import typer
from rich.syntax import Syntax

from ci_doctor.cli._context import config_files
from ci_doctor.cli._output import LessPager, console, no_color, plain
from ci_doctor.config.dump import as_yaml, diff_lines, schema_json
from ci_doctor.config.loader import default_config, load_config
from ci_doctor.service.setup import validate as validate_setup

NAME = "config"


def run(
    diff: bool = typer.Option(
        False, "--diff", help="Show only what your config changes against the shipped defaults."
    ),
    schema: bool = typer.Option(False, "--schema", help="Print the JSON Schema for .ci-doctor.yml and exit."),
    validate: bool = typer.Option(
        False, "--validate", help="Load and validate the merged config, then report what failed."
    ),
    less: bool = typer.Option(False, "--less", help="Force the scrollable pager, even when piped."),
) -> None:
    """Show the effective config after every layer is merged.

    Useful for answering "why is it doing that" — the answer is almost always a
    layer you forgot was applied. Pages the output on a terminal; pipe it (or pass
    the global `--plain`) to get the raw text back.
    """
    if validate:
        typer.echo(validate_setup(config_files()))
        return
    if schema:
        text, lexer = schema_json(), "json"
    elif not diff:
        text, lexer = as_yaml(load_config(config_files())), "yaml"
    else:
        lines = diff_lines(as_yaml(default_config()), as_yaml(load_config(config_files())))
        text, lexer = "\n".join(lines) or "no differences: running on the shipped defaults.", "diff"

    if plain() or (not less and not sys.stdout.isatty()):
        # Raw echo, not rich: piped output is meant to be re-parsed, and a Console
        # would pad every line to the detected terminal width.
        typer.echo(text)
        return
    out = console(soft_wrap=True)  # soft_wrap: never fold long regexes
    with out.pager(LessPager(), styles=not no_color()):
        out.print(Syntax(text, lexer, theme="ansi_dark", background_color="default"))
