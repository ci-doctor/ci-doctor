"""`ci-doctor mcp`."""

from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import files
from ci_doctor.config.files import ConfigFiles
from ci_doctor.config.loader import REPO_CONFIG


class McpCommand(Command):
    """Serve ci-doctor to a coding agent over MCP, on stdio.

    The agent is the model: it gets each failed job's evidence and explains it,
    and can pick matchers for the project. Matcher edits go to `./.ci-doctor.yml`
    unless `--write-to` names another file.
    """

    name = "mcp"

    @override
    def run(self) -> None:
        from ci_doctor.mcp.server import build  # the SDK is only worth importing for this command

        given = files()
        # The project's file, not the user config `matchers add` defaults to: an agent
        # picks matchers for the repository it is working in, and the team shares them.
        build(ConfigFiles(given.files, given.write_to or REPO_CONFIG)).run("stdio")
