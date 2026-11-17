"""Antigravity IDE: `mcp_config.json` under `~/.gemini`. It has no project config."""

from pathlib import Path
from typing import Any, ClassVar

from ci_doctor.harness.entry import SERVER, server_entry
from ci_doctor.harness.formats import Change, safely
from ci_doctor.harness.formats.json import unset_json
from ci_doctor.harness.targets.base import GLOBAL_ONLY, Location, McpJsonTarget

#: Antigravity keeps its files under Gemini CLI's folder, `~/.gemini`.
_GEMINI = ".gemini"
_CONFIG = "mcp_config.json"


class Antigravity(McpJsonTarget):
    """Antigravity. Its instructions file is Gemini CLI's `GEMINI.md`, so that is left to the Gemini target."""

    id = "antigravity"
    name = "Antigravity"
    locations: ClassVar[frozenset[Location]] = GLOBAL_ONLY

    def home(self) -> Path:  # noqa: D102 - documented on McpJsonTarget
        return Path.home() / _GEMINI / "antigravity"

    def detected(self) -> bool:  # noqa: D102 - documented on Target
        return self.home().is_dir() or self._migrated().exists()

    def config(self, location: Location) -> Path:  # noqa: D102 - documented on McpJsonTarget
        # Newer Antigravity moved its config and leaves a `.migrated` marker behind.
        return self._unified() if self._migrated().exists() else self.home() / _CONFIG

    def entry(self, location: Location) -> dict[str, Any]:  # noqa: D102 - documented on McpJsonTarget
        return server_entry(with_type=False)  # Antigravity rejects `"type": "stdio"`

    def configured(self, location: Location) -> bool:  # noqa: D102 - documented on Target
        return any(self._lists_server(path) for path in self._both())

    def uninstall(self, location: Location) -> list[Change]:  # noqa: D102 - documented on Target
        return [safely(path, lambda path=path: unset_json(path, (self.key, SERVER))) for path in self._both()]

    def _both(self) -> list[Path]:
        """The old and the new config file: an entry may sit in either after Antigravity migrates."""
        return [self.home() / _CONFIG, self._unified()]

    def _unified(self) -> Path:
        return Path.home() / _GEMINI / "config" / _CONFIG

    def _migrated(self) -> Path:
        return Path.home() / _GEMINI / "config" / ".migrated"
