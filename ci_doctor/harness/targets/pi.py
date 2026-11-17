"""pi: `mcp.json` and `AGENTS.md`, in the project (`.pi/`) or in `~/.pi/agent`.

pi has no MCP of its own; the `pi-mcp-adapter` extension adds it and reads `mcpServers`
from these files.
"""

import os
from pathlib import Path
from typing import Any, ClassVar

from ci_doctor.harness.entry import server_entry
from ci_doctor.harness.targets.base import Location, McpJsonTarget


class Pi(McpJsonTarget):
    """pi, the coding agent. Its MCP servers need the `pi-mcp-adapter` extension."""

    id = "pi"
    name = "pi"
    binary: ClassVar[str | None] = "pi"
    requires: ClassVar[str | None] = "the pi-mcp-adapter extension: pi install npm:pi-mcp-adapter"

    def home(self) -> Path:  # noqa: D102 - documented on McpJsonTarget
        # pi moves its whole agent folder with PI_CODING_AGENT_DIR.
        return Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent")

    def config(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return (self.home() if location == Location.GLOBAL else Path.cwd() / ".pi") / "mcp.json"

    def instructions(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return (self.home() if location == Location.GLOBAL else Path.cwd()) / "AGENTS.md"

    def entry(self, location: Location) -> dict[str, Any]:  # noqa: D102 - documented on McpJsonTarget
        return server_entry(with_type=False)  # the adapter's own examples carry no `type`
