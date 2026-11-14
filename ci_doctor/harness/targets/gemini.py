"""Gemini CLI: `.gemini/settings.json` and `GEMINI.md`, in the project or in `~/.gemini`."""

from pathlib import Path
from typing import ClassVar

from ci_doctor.harness.targets.base import Location, McpJsonTarget


class GeminiCli(McpJsonTarget):
    """Gemini CLI."""

    id = "gemini"
    name = "Gemini CLI"
    binary: ClassVar[str | None] = "gemini"

    def home(self) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return Path.home() / ".gemini"

    def config(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return (self.home() if location == Location.GLOBAL else Path.cwd() / ".gemini") / "settings.json"

    def instructions(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        # In a project, Gemini reads GEMINI.md from the root, not from `.gemini/`.
        return (self.home() if location == Location.GLOBAL else Path.cwd()) / "GEMINI.md"
