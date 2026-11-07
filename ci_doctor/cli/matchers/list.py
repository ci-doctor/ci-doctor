"""`ci-doctor matchers list`."""

from dataclasses import dataclass

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import Row, say, table
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import resolve, session

NAME = "list"


@dataclass(frozen=True)
class _Included(Row):
    matcher: str
    via: str
    classification: str

    def line(self) -> str:
        return f"{self.matcher}  via {self.via}  [{self.classification}]"


def run() -> None:
    """List what `matchers.include` resolves to, and how each matcher got in."""
    cfg = editable().matchers
    if not cfg.include:
        say("nothing included. Run `ci-doctor matchers add <registry>/<name>`.", "yellow")
        return
    with session(cfg) as http:
        resolved = resolve(cfg, http)
    rows = []
    for key, via in resolved.matchers.items():
        ref = Ref.parse(key)
        rows.append(
            _Included(key, via, ", ".join(resolved.indexes[ref.alias].matchers[ref.name].classification))
        )
    table(_Included, rows)
