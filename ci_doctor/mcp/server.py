"""The MCP server and its tools, one per thing an agent can ask of ci-doctor."""

import functools
from collections.abc import Callable
from importlib import resources
from typing import ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ci_doctor import __version__
from ci_doctor.config.files import ConfigFiles
from ci_doctor.config.loader import load_config
from ci_doctor.core.redact import redact_text
from ci_doctor.errors import SetupError
from ci_doctor.service import matchers
from ci_doctor.service.brief import Brief, run_briefs
from ci_doctor.service.setup import validate

_P = ParamSpec("_P")
_T = TypeVar("_T")

_READ = ToolAnnotations(read_only_hint=True)
#: Reads a CI system or a registry: the answer can differ between two calls.
_READ_REMOTE = ToolAnnotations(read_only_hint=True, open_world_hint=True)
_EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)


class Selected(BaseModel):
    """What the config selects today."""

    included: list[matchers.Included] = Field(description="Every matcher `matchers.include` resolves to.")
    excluded: list[str] = Field(description="Outputs `matchers.exclude` drops.")


class Change(BaseModel):
    """What an edit to `matchers.include` did."""

    file: str = Field(description="The config file edited.")
    changed: list[str] = Field(description="The refs added or removed.")
    skipped: list[matchers.Skipped] = Field(default_factory=list, description="Refs left as they were.")


def _reported(tool: Callable[_P, _T]) -> Callable[_P, _T]:
    """Turn a tool's failure into a message the agent can read and act on.

    The SDK shows an unexpected exception only as "Error executing tool", which
    leaves the agent nothing to fix. A failure here is a bad id, a missing token
    or an unreachable host, so its text is passed on, redacted (invariant #5).

    Args:
        tool: The tool function.

    Returns:
        The tool, raising only `ToolError`.
    """

    @functools.wraps(tool)
    def reported(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return tool(*args, **kwargs)
        except SetupError as err:
            raise ToolError(str(err)) from None
        except Exception as err:  # noqa: BLE001 - one failed call is an answer, not a dead server
            # The built-in rules, not the loaded config's: loading it may be what failed.
            raise ToolError(redact_text(f"{type(err).__name__}: {err}")) from None

    return reported


def build(files: ConfigFiles) -> MCPServer:
    """The server, its tools bound to one set of config files.

    The config is loaded per call, not here: an agent that has just edited it
    must see the edit on its next call.

    Args:
        files: The config files the tools read, and the one the matcher tools edit.

    Returns:
        The server, not yet running.
    """
    server = MCPServer(
        "ci-doctor",
        version=__version__,
        instructions=resources.files("ci_doctor.llm.prompts").joinpath("mcp.instructions.txt").read_text(),
    )

    @server.tool(annotations=_READ_REMOTE)
    @_reported
    def analyze_run(run_id: str, job_id: str | None = None) -> list[Brief]:
        """Brief each failed job of a CI pipeline/run, for you to explain: phase, evidence, instructions.

        Pass job_id to look at a single job. Empty when the run has no failed job.
        """
        return run_briefs(run_id, load_config(files.paths()), job_id)

    @server.tool(annotations=_READ_REMOTE)
    @_reported
    def search_matchers(term: str = "", registry: str | None = None) -> list[matchers.Hit]:
        """Find matchers and bundles by name, description or tag; an empty term lists everything.

        Each hit's name is what add_matchers takes.
        """
        return matchers.search(files.editable().matchers, term, registry)

    @server.tool(annotations=_READ_REMOTE)
    @_reported
    def list_matchers() -> Selected:
        """Show the matchers the config selects now, and which bundle brought each one in."""
        cfg = files.editable().matchers
        return Selected(included=matchers.included(cfg), excluded=cfg.exclude)

    @server.tool(annotations=_EDIT)
    @_reported
    def add_matchers(refs: list[str]) -> Change:
        """Add matchers or bundles (`registry/name`) to `matchers.include` in the config file."""
        result = matchers.add(files, refs)
        return Change(file=str(files.target()), changed=result.added, skipped=result.skipped)

    @server.tool(annotations=_EDIT)
    @_reported
    def remove_matchers(refs: list[str]) -> Change:
        """Remove entries from `matchers.include` in the config file."""
        return Change(file=str(files.target()), changed=matchers.remove(files, refs))

    @server.tool(annotations=_READ_REMOTE)
    @_reported
    def check_setup() -> str:
        """Validate the config and load every selected matcher; returns a one-line summary."""
        return validate(files.paths())

    return server
