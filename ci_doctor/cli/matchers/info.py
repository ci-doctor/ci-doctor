"""`ci-doctor matchers info`."""

from dataclasses import dataclass

import httpx
import typer
from rich.text import Text

from ci_doctor.cli._context import editable
from ci_doctor.cli._output import Cell, Row, say, table
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import Resolved, check_ref, fetch_matcher, redact, session, via

NAME = "info"


@dataclass(frozen=True)
class _Field(Row):
    field: Cell
    value: str

    def line(self) -> str:
        return f"{self.field}: {self.value}"


@dataclass(frozen=True)
class _Member(Row):
    matcher: str

    def line(self) -> str:
        return f"  {self.matcher}"


def run(ref: str = typer.Argument(..., metavar="ALIAS/NAME")) -> None:
    """Show one matcher or bundle: what it does, where it lives, what a bundle holds."""
    cfg = editable().matchers
    seen = Resolved()
    with session(cfg) as http:
        parsed = check_ref(ref, cfg, http, seen)
        is_matcher = parsed.name in seen.indexes[parsed.alias].matchers
        fields = _fields(parsed, seen, http)
        members = [] if is_matcher else list(via(cfg, [ref], http))
    say(f"{ref} ({'matcher' if is_matcher else 'bundle'})", "bold")
    table(_Field, fields, header=False)
    if members:
        say("matchers:", "bold")
        table(_Member, map(_Member, members), header=False)


def _fields(ref: Ref, seen: Resolved, http: httpx.Client) -> list[_Field]:
    """What `info` shows about one served matcher or bundle.

    Args:
        ref: A ref its registry serves, already checked.
        seen: Registries already read, holding `ref`'s.
        http: The command's HTTP client, to fetch a matcher's role.

    Returns:
        The rows, in display order.
    """
    served = seen.indexes[ref.alias]
    entry = served.matchers.get(ref.name) or served.bundles[ref.name]
    pairs = [("description", entry.description)]
    if ref.name in served.matchers:
        pairs += [
            ("classification", ", ".join(served.matchers[ref.name].classification)),
            ("role", fetch_matcher(ref, seen, http).role),
        ]
    pairs += [("registry", redact(seen.locations[ref.alias])), ("path", entry.path)]
    return [_Field(Text(name, "dim"), value) for name, value in pairs]
