"""`--json`: a listing command's result as one JSON document, for scripts."""

import json
from dataclasses import asdict, is_dataclass
from typing import Any

import typer

#: The `--json` option every listing command takes.
JSON_OPTION = typer.Option(False, "--json", help="Print the result as JSON instead of a table, for scripts.")


def emit(data: Any) -> None:  # noqa: ANN401 - any JSON-ready value
    """Print `data` as indented JSON; dataclasses become objects.

    Args:
        data: A dict, list, dataclass or scalar.
    """
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=_plain))


def _plain(value: Any) -> Any:  # noqa: ANN401 - whatever json could not encode
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)
