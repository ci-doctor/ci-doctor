"""Claude Code: `.mcp.json` (project) or `~/.claude.json` (user), `CLAUDE.md`, and an allow list."""

from pathlib import Path
from typing import ClassVar

from ci_doctor.agent.harness.entry import READ_ONLY_TOOLS, SERVER
from ci_doctor.agent.harness.targets.base import Location, McpJsonTarget
from ci_doctor.lib.edit import Change, safely
from ci_doctor.lib.edit.json import add_json_items, drop_json_items


class ClaudeCode(McpJsonTarget):
    """Claude Code. The only agent here with an allow list, in `settings.json`."""

    id = "claude"
    name = "Claude Code"
    binary: ClassVar[str | None] = "claude"

    def home(self) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return Path.home() / ".claude"

    def config(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return Path.home() / ".claude.json" if location == Location.GLOBAL else Path.cwd() / ".mcp.json"

    def instructions(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return self._dir(location) / "CLAUDE.md"

    def install(self, location: Location, *, permissions: bool) -> list[Change]:  # noqa: D102 - documented on Target
        changes = super().install(location, permissions=permissions)
        if permissions:
            settings = self._settings(location)
            allow = [f"mcp__{SERVER}__{tool}" for tool in READ_ONLY_TOOLS]
            changes.append(
                safely(settings, lambda: add_json_items(settings, ("permissions", "allow"), allow))
            )
        return changes

    def uninstall(self, location: Location) -> list[Change]:  # noqa: D102 - documented on Target
        settings = self._settings(location)
        prefix = f"mcp__{SERVER}__"
        return [
            *super().uninstall(location),
            safely(settings, lambda: drop_json_items(settings, ("permissions", "allow"), prefix)),
        ]

    def paths(self, location: Location) -> list[Path]:  # noqa: D102 - documented on Target
        return [*super().paths(location), self._settings(location)]

    def _dir(self, location: Location) -> Path:
        return self.home() if location == Location.GLOBAL else Path.cwd() / ".claude"

    def _settings(self, location: Location) -> Path:
        return self._dir(location) / "settings.json"
