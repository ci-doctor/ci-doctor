"""`ci-doctor matchers info`."""

from dataclasses import dataclass

import httpx
import typer
from rich.text import Text
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Cell, Row, table
from ci_doctor.matchers.cache import Cache
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import Resolved, check_ref, fetch_outputs, redact, session, via


class InfoCommand(Command):
    """Show one matcher or bundle: what it does, where it lives, what a bundle holds."""

    name = "info"

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

    @override
    def run(self, ref: str = typer.Argument(..., metavar="ALIAS/NAME")) -> None:
        cfg = editable().matchers
        seen = Resolved(cache=Cache.open(cfg))
        with session(cfg) as http:
            parsed = check_ref(ref, cfg, http, seen)
            is_matcher = parsed.name in seen.indexes[parsed.alias].matchers
            fields = self._fields(parsed, seen, http)
            members = [] if is_matcher else list(via(cfg, [ref], http))
        say(f"{ref} ({'matcher' if is_matcher else 'bundle'})", "bold")
        table(self._Field, fields, header=False)
        if members:
            say("matchers:", "bold")
            table(self._Member, map(self._Member, members), header=False)

    def _fields(self, ref: Ref, seen: Resolved, http: httpx.Client) -> list["InfoCommand._Field"]:
        """What `info` shows about one served matcher or bundle.

        Args:
            ref: A ref its registry serves, already checked.
            seen: Registries already read, holding `ref`'s.
            http: The command's HTTP client, to fetch a matcher's outputs and roles.

        Returns:
            The rows, in display order.
        """
        served = seen.indexes[ref.alias]
        entry = served.matchers.get(ref.name) or served.bundles[ref.name]
        pairs = [("description", entry.description)]
        if ref.name in served.matchers:
            pairs.append(("tags", ", ".join(served.matchers[ref.name].tags)))
            outputs = fetch_outputs(ref, seen, http)
            if len(outputs) == 1:
                pairs.append(("role", outputs[0].role))
            else:
                pairs += [("output", f"{o.id} [{o.role}]") for o in outputs]
        pairs += [("registry", redact(seen.locations[ref.alias])), ("path", entry.path)]
        return [self._Field(Text(name, "dim"), value) for name, value in pairs]
