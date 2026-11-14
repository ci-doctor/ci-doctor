"""What ci-doctor puts in an agent's config: the server entry and the instructions block."""

import shutil
from importlib import resources
from typing import Any

#: The server's name in every agent config, and the `mcp__ci-doctor__*` tool prefix.
SERVER = "ci-doctor"
#: The command an agent runs to start the server.
COMMAND = "ci-doctor"
#: Tools that change nothing. Pinned to the server's own annotations by `test_harness.py`.
READ_ONLY_TOOLS = ("analyze_run", "search_matchers", "list_matchers", "check_setup")


def on_path() -> bool:
    """Whether agents can start the server: they run `ci-doctor` by name, so it must be on PATH."""
    return shutil.which(COMMAND) is not None


def instructions() -> str:
    """The instructions agents read about ci-doctor, without the markers."""
    return resources.files("ci_doctor.llm.prompts").joinpath("harness.instructions.md").read_text().strip()


def server_entry(*args: str, with_type: bool = True) -> dict[str, Any]:
    """The `mcpServers` entry most agents take.

    Args:
        *args: Arguments after `mcp`, such as `--project <dir>`.
        with_type: Include `"type": "stdio"`; Antigravity rejects it.

    Returns:
        The entry.
    """
    entry: dict[str, Any] = {"type": "stdio"} if with_type else {}
    return {**entry, "command": COMMAND, "args": ["mcp", *args]}
