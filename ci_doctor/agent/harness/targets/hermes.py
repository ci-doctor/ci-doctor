"""Hermes Agent: `mcp_servers` in `$HERMES_HOME/config.yaml`, edited as text so comments stay. No project config."""

import os
import shutil
from pathlib import Path
from typing import Any, ClassVar

import yaml

from ci_doctor.agent.harness.entry import COMMAND, SERVER
from ci_doctor.agent.harness.targets.base import GLOBAL_ONLY, Location, Target
from ci_doctor.lib.edit import Change, Unsafe, safely
from ci_doctor.lib.edit.yaml import (
    Lines,
    add_item,
    edit_yaml,
    remove_child,
    remove_item,
    set_child,
)

#: Hermes names a server's tools `mcp-<server>`; a CLI profile may list the ones it allows.
_TOOLSET = f"mcp-{SERVER}"
_ENTRY: dict[str, Any] = {
    "command": COMMAND,
    "args": ["mcp"],
    "timeout": 120,
    "connect_timeout": 60,
    "enabled": True,
}


class Hermes(Target):
    """Hermes Agent."""

    id = "hermes"
    name = "Hermes Agent"
    locations: ClassVar[frozenset[Location]] = GLOBAL_ONLY

    def detected(self) -> bool:  # noqa: D102 - documented on Target
        return self._home().is_dir() or shutil.which("hermes") is not None

    def configured(self, location: Location) -> bool:  # noqa: D102 - documented on Target
        try:
            config = yaml.safe_load(self._config().read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return False
        return isinstance(config, dict) and SERVER in (config.get("mcp_servers") or {})

    def install(self, location: Location, *, permissions: bool) -> list[Change]:  # noqa: D102 - documented on Target
        config = self._config()
        return [safely(config, lambda: edit_yaml(config, _add_text, _add))]

    def uninstall(self, location: Location) -> list[Change]:  # noqa: D102 - documented on Target
        config = self._config()
        return [safely(config, lambda: edit_yaml(config, _remove_text, _remove, removing=True))]

    def snippet(self, location: Location) -> str:  # noqa: D102 - documented on Target
        body = yaml.safe_dump({"mcp_servers": {SERVER: _ENTRY}}, sort_keys=False)
        return f"# Add to {self._config()}\n# If platform_toolsets.cli lists toolsets, add {_TOOLSET} to it.\n\n{body}"

    def paths(self, location: Location) -> list[Path]:  # noqa: D102 - documented on Target
        return [self._config()]

    def _home(self) -> Path:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")

    def _config(self) -> Path:
        return self._home() / "config.yaml"


def _add_text(lines: Lines) -> Lines:
    return add_item(set_child(lines, "mcp_servers", SERVER, _ENTRY), "platform_toolsets", "cli", _TOOLSET)


def _remove_text(lines: Lines) -> Lines:
    return remove_item(remove_child(lines, "mcp_servers", SERVER), "platform_toolsets", "cli", _TOOLSET)


def _add(config: dict) -> None:
    servers = config.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise Unsafe("config.yaml: `mcp_servers` is not a mapping")
    servers[SERVER] = dict(_ENTRY)
    cli = (config.get("platform_toolsets") or {}).get("cli")
    if isinstance(cli, list) and _TOOLSET not in cli:
        cli.append(_TOOLSET)  # without it, a profile that lists its toolsets hides ours


def _remove(config: dict) -> None:
    servers = config.get("mcp_servers")
    if isinstance(servers, dict) and SERVER in servers:
        del servers[SERVER]
        if not servers:
            del config["mcp_servers"]
    cli = (config.get("platform_toolsets") or {}).get("cli")
    if isinstance(cli, list) and _TOOLSET in cli:
        cli.remove(_TOOLSET)
