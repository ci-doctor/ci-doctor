"""opencode: `opencode.json(c)` and `AGENTS.md`, in the project or in `~/.config/opencode`."""

import os
from pathlib import Path
from typing import Any, ClassVar

from ci_doctor.agent.harness.entry import COMMAND
from ci_doctor.agent.harness.targets.base import Location, McpJsonTarget


class Opencode(McpJsonTarget):
    """opencode. Its servers sit under `mcp`, each with the whole command line in one list."""

    id = "opencode"
    name = "opencode"
    binary: ClassVar[str | None] = "opencode"
    key: ClassVar[str] = "mcp"
    new_file: ClassVar[dict[str, Any] | None] = {"$schema": "https://opencode.ai/config.json"}

    def home(self) -> Path:  # noqa: D102 - documented on McpJsonTarget
        # opencode uses XDG_CONFIG_HOME, else ~/.config, on every platform.
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"

    def config(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        folder = self.home() if location == Location.GLOBAL else Path.cwd()
        plain = folder / "opencode.json"
        return plain if plain.exists() else folder / "opencode.jsonc"

    def instructions(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return (self.home() if location == Location.GLOBAL else Path.cwd()) / "AGENTS.md"

    def entry(self, location: Location) -> dict[str, Any]:  # noqa: D102 - documented on McpJsonTarget
        return {"type": "local", "command": [COMMAND, "mcp"], "enabled": True}
