"""Cursor: `.cursor/mcp.json`, in the project or in `~/.cursor`."""

from pathlib import Path
from typing import Any, ClassVar

from ci_doctor.harness.entry import server_entry
from ci_doctor.harness.targets.base import Location, McpJsonTarget


class Cursor(McpJsonTarget):
    """Cursor. It starts MCP servers outside the project, so the entry names the project."""

    id = "cursor"
    name = "Cursor"
    binary: ClassVar[str | None] = "cursor"

    def home(self) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return Path.home() / ".cursor"

    def config(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return (self.home() if location == Location.GLOBAL else Path.cwd() / ".cursor") / "mcp.json"

    def entry(self, location: Location) -> dict[str, Any]:  # noqa: D102 - documented on McpJsonTarget
        # Cursor fills in `${workspaceFolder}` with the open project, so a committed file works for everyone.
        return server_entry("--project", "${workspaceFolder}")
