"""`ci-doctor matchers info`."""

from dataclasses import dataclass
from typing import Any

import httpx
import typer
from rich.text import Text
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import editable
from ci_doctor.cli._lib.outputs.data import JSON_OPTION, emit
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.outputs.table import Cell, Row, table
from ci_doctor.matchers.models import Ref
from ci_doctor.registry.cache import Cache
from ci_doctor.registry.resolve import Resolved, check_ref, fetch_outputs, redact, session, via


class InfoCommand(Command):
    """Show one matcher or bundle: what it does, where it lives, what a bundle holds."""

    name = "info"
    examples = (
        "ci-doctor matchers info official/pytest",
        "ci-doctor matchers info official/python --json   # a bundle and its members",
    )

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
    def run(
        self, ref: str = typer.Argument(..., metavar="ALIAS/NAME"), json_output: bool = JSON_OPTION
    ) -> None:
        cfg = editable().matchers
        seen = Resolved(cache=Cache.open(cfg))
        with session(cfg) as http:
            info = self._describe(check_ref(ref, cfg, http, seen), seen, http)
            if info["kind"] == "bundle":
                info["matchers"] = list(via(cfg, [ref], http))
        if json_output:
            emit(info)
            return
        say(f"{ref} ({info['kind']})", "bold")
        table(self._Field, self._fields(info), header=False)
        if info.get("matchers"):
            say("matchers:", "bold")
            table(self._Member, map(self._Member, info["matchers"]), header=False)

    def _describe(self, ref: Ref, seen: Resolved, http: httpx.Client) -> dict[str, Any]:
        """What `info` knows about one served matcher or bundle.

        Args:
            ref: A ref its registry serves, already checked.
            seen: Registries already read, holding `ref`'s.
            http: The command's HTTP client, to fetch a matcher's outputs and roles.

        Returns:
            Its name, kind, description, registry and path; a matcher's tags and outputs too.
        """
        served = seen.indexes[ref.alias]
        entry = served.matchers.get(ref.name) or served.bundles[ref.name]
        info: dict[str, Any] = {
            "name": str(ref),
            "kind": "matcher" if ref.name in served.matchers else "bundle",
            "description": entry.description,
        }
        if info["kind"] == "matcher":
            info["tags"] = entry.tags
            info["outputs"] = [{"id": o.id, "role": o.role} for o in fetch_outputs(ref, seen, http)]
        return {**info, "registry": redact(seen.locations[ref.alias]), "path": entry.path}

    def _fields(self, info: dict[str, Any]) -> list["InfoCommand._Field"]:
        """The rows `info` prints: one output shows as its role, several as one line each."""
        pairs = [("description", info["description"])]
        if info["kind"] == "matcher":
            pairs.append(("tags", ", ".join(info["tags"])))
            outputs = info["outputs"]
            if len(outputs) == 1:
                pairs.append(("role", outputs[0]["role"]))
            else:
                pairs += [("output", f"{o['id']} [{o['role']}]") for o in outputs]
        pairs += [("registry", info["registry"]), ("path", info["path"])]
        return [self._Field(Text(name, "dim"), value) for name, value in pairs]
