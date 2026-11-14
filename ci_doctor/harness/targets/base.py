"""What every agent target does, and a base for the agents that keep MCP servers in JSON."""

import json
import shutil
from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from ci_doctor.harness.entry import SERVER, server_entry
from ci_doctor.harness.formats import Change, safely
from ci_doctor.harness.formats.json_keys import set_json, unset_json
from ci_doctor.harness.formats.markdown import START, set_block, unset_block


class Location(StrEnum):
    """Which config an agent target edits."""

    #: This project's files, which a team can commit.
    LOCAL = "local"
    #: The user's own files, for every project.
    GLOBAL = "global"


BOTH = frozenset({Location.LOCAL, Location.GLOBAL})
GLOBAL_ONLY = frozenset({Location.GLOBAL})


class Target(ABC):
    """One coding agent: where its config lives, and how to add or remove ci-doctor there."""

    id: ClassVar[str]
    name: ClassVar[str]
    locations: ClassVar[frozenset[Location]] = BOTH

    @abstractmethod
    def detected(self) -> bool:
        """Whether this agent seems to be on this machine: its config folder or its command."""

    @abstractmethod
    def configured(self, location: Location) -> bool:
        """Whether ci-doctor's server is in this agent's config at `location`."""

    @abstractmethod
    def install(self, location: Location, *, permissions: bool) -> list[Change]:
        """Add ci-doctor to this agent; safe to run again.

        Args:
            location: Which config to write.
            permissions: Also auto-allow the read-only tools, where the agent has an allow list.

        Returns:
            One change per file touched.
        """

    @abstractmethod
    def uninstall(self, location: Location) -> list[Change]:
        """Remove what `install` added, and nothing else.

        Args:
            location: Which config to edit.

        Returns:
            One change per file looked at.
        """

    @abstractmethod
    def snippet(self, location: Location) -> str:
        """The config to add by hand, with the file it goes in. Writes nothing."""

    @abstractmethod
    def paths(self, location: Location) -> list[Path]:
        """The files `install` writes at `location`."""

    def present(self, location: Location) -> bool:
        """Whether anything of ci-doctor's is here: its server, its instructions, or its allow entries.

        Args:
            location: Which config to look in.

        Returns:
            True if `uninstall` would have something to remove.
        """
        if self.configured(location):
            return True
        for path in self.paths(location):
            text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            if START in text or f"mcp__{SERVER}__" in text:
                return True
        return False


class McpJsonTarget(Target):
    """An agent that reads its MCP servers from one key of a JSON file, `mcpServers` for most."""

    #: The agent's command, for detection.
    binary: ClassVar[str | None] = None
    #: The key holding the servers.
    key: ClassVar[str] = "mcpServers"
    #: What a new config file starts with.
    new_file: ClassVar[dict[str, Any] | None] = None

    @abstractmethod
    def config(self, location: Location) -> Path:
        """The JSON file holding the servers."""

    @abstractmethod
    def home(self) -> Path:
        """The agent's own folder; its presence means the agent is installed."""

    def instructions(self, location: Location) -> Path | None:
        """The Markdown file the agent reads instructions from, or None if it has none."""
        return None

    def entry(self, location: Location) -> dict[str, Any]:
        """ci-doctor's entry under `key`."""
        return server_entry()

    def detected(self) -> bool:  # noqa: D102 - documented on Target
        return self.home().is_dir() or (self.binary is not None and shutil.which(self.binary) is not None)

    def configured(self, location: Location) -> bool:  # noqa: D102 - documented on Target
        return self._lists_server(self.config(location))

    def _lists_server(self, path: Path) -> bool:
        """Whether this JSON file has ci-doctor under `key`."""
        try:
            return SERVER in json.loads(path.read_text(encoding="utf-8")).get(self.key, {})
        except (OSError, ValueError, AttributeError):
            return False

    def install(self, location: Location, *, permissions: bool) -> list[Change]:  # noqa: D102 - documented on Target
        config, entry = self.config(location), self.entry(location)
        changes = [safely(config, lambda: set_json(config, (self.key, SERVER), entry, new=self.new_file))]
        if (notes := self.instructions(location)) is not None:
            changes.append(set_block(notes))
        return changes

    def uninstall(self, location: Location) -> list[Change]:  # noqa: D102 - documented on Target
        config = self.config(location)
        changes = [safely(config, lambda: unset_json(config, (self.key, SERVER), new=self.new_file))]
        if (notes := self.instructions(location)) is not None:
            changes.append(unset_block(notes))
        return changes

    def snippet(self, location: Location) -> str:  # noqa: D102 - documented on Target
        body = json.dumps({self.key: {SERVER: self.entry(location)}}, indent=2)
        return f"# Add to {self.config(location)}\n\n{body}\n"

    def paths(self, location: Location) -> list[Path]:  # noqa: D102 - documented on Target
        notes = self.instructions(location)
        return [self.config(location), *([notes] if notes else [])]
