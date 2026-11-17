"""Kiro: `.kiro/settings/mcp.json`, and a steering file, in the project or in `~/.kiro`."""

from pathlib import Path
from typing import ClassVar

from ci_doctor.agent.harness.entry import instructions
from ci_doctor.agent.harness.targets.base import Location, McpJsonTarget
from ci_doctor.lib.edit import Change
from ci_doctor.lib.edit.markdown import set_file, unset_block


class Kiro(McpJsonTarget):
    """Kiro. It reads every Markdown file in `steering/`, so ci-doctor gets a file of its own."""

    id = "kiro"
    name = "Kiro"
    binary: ClassVar[str | None] = "kiro-cli"

    def home(self) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return Path.home() / ".kiro"

    def config(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return self._dir(location) / "settings" / "mcp.json"

    def install(self, location: Location, *, permissions: bool) -> list[Change]:  # noqa: D102 - documented on Target
        return [
            *super().install(location, permissions=permissions),
            set_file(self._steering(location), instructions()),
        ]

    def uninstall(self, location: Location) -> list[Change]:  # noqa: D102 - documented on Target
        return [*super().uninstall(location), unset_block(self._steering(location))]

    def paths(self, location: Location) -> list[Path]:  # noqa: D102 - documented on Target
        return [*super().paths(location), self._steering(location)]

    def _dir(self, location: Location) -> Path:
        return self.home() if location == Location.GLOBAL else Path.cwd() / ".kiro"

    def _steering(self, location: Location) -> Path:
        return self._dir(location) / "steering" / "ci-doctor.md"
