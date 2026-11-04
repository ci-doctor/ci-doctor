"""`ci-doctor matchers list`."""

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import say, table
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import resolve, session

NAME = "list"


def run() -> None:
    """List what `matchers.include` resolves to, and how each matcher got in."""
    cfg = editable().matchers
    if not cfg.include:
        say("nothing included. Run `ci-doctor matchers add <registry>/<name>`.", "yellow")
        return
    with session() as http:
        resolved = resolve(cfg, http)
    rows = []
    for key, via in resolved.matchers.items():
        ref = Ref.parse(key)
        rows.append((key, via, ", ".join(resolved.indexes[ref.alias].matchers[ref.name].classification)))
    table(["Matcher", "Via", "Classification"], rows, lambda key, via, tags: f"{key}  via {via}  [{tags}]")
