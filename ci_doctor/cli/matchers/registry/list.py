"""`ci-doctor matchers registry list`."""

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import table
from ci_doctor.matchers.registry import redact

NAME = "list"


def run() -> None:
    """List every configured registry."""
    rows = [(alias, redact(location)) for alias, location in editable().matchers.registries.items()]
    table(["Registry", "Location"], rows, lambda alias, location: f"{alias}  {location}")
