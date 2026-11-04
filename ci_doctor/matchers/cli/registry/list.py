"""`ci-doctor matchers registry list`."""

from ci_doctor.matchers.cli._common import effective, table
from ci_doctor.matchers.registry import redact

NAME = "list"


def run() -> None:
    """List every configured registry."""
    rows = [(alias, redact(location)) for alias, location in effective().registries.items()]
    table(["Registry", "Location"], rows, lambda alias, location: f"{alias}  {location}")
