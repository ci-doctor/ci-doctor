"""Codex CLI: `[mcp_servers.ci-doctor]` in `~/.codex/config.toml`, and `AGENTS.md`. No project config."""

import os
import shutil
import tomllib
from pathlib import Path
from typing import ClassVar

from ci_doctor.harness.entry import COMMAND, SERVER
from ci_doctor.harness.formats import Change, safely
from ci_doctor.harness.formats.markdown import set_block, unset_block
from ci_doctor.harness.formats.toml_table import set_toml_table, unset_toml_table
from ci_doctor.harness.targets.base import GLOBAL_ONLY, Location, Target

_TABLE = f"mcp_servers.{SERVER}"
_BODY = [f'command = "{COMMAND}"', 'args = ["mcp"]']


class Codex(Target):
    """Codex CLI. Its config is TOML, edited as text so the user's comments stay."""

    id = "codex"
    name = "Codex CLI"
    locations: ClassVar[frozenset[Location]] = GLOBAL_ONLY

    def detected(self) -> bool:  # noqa: D102 - documented on Target
        return self._home().is_dir() or shutil.which("codex") is not None

    def configured(self, location: Location) -> bool:  # noqa: D102 - documented on Target
        try:
            config = tomllib.loads(self._config().read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return False
        return SERVER in config.get("mcp_servers", {})

    def install(self, location: Location, *, permissions: bool) -> list[Change]:  # noqa: D102 - documented on Target
        config = self._config()
        return [
            safely(config, lambda: set_toml_table(config, _TABLE, _BODY)),
            set_block(self._instructions()),
        ]

    def uninstall(self, location: Location) -> list[Change]:  # noqa: D102 - documented on Target
        config = self._config()
        return [safely(config, lambda: unset_toml_table(config, _TABLE)), unset_block(self._instructions())]

    def snippet(self, location: Location) -> str:  # noqa: D102 - documented on Target
        return f"# Add to {self._config()}\n\n[{_TABLE}]\n" + "\n".join(_BODY) + "\n"

    def paths(self, location: Location) -> list[Path]:  # noqa: D102 - documented on Target
        return [self._config(), self._instructions()]

    def _home(self) -> Path:
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")

    def _config(self) -> Path:
        return self._home() / "config.toml"

    def _instructions(self) -> Path:
        return self._home() / "AGENTS.md"
