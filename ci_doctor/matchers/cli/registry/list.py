"""`ci-doctor matchers registry list`."""

from pathlib import Path

import typer

from ci_doctor.matchers.cli._common import CONFIG, effective
from ci_doctor.matchers.registry import redact

NAME = "list"


def run(config: Path = CONFIG) -> None:
    """List every configured registry."""
    for alias, location in effective(config).registries.items():
        typer.echo(f"{alias}  {redact(location)}")
