"""`ci-doctor mcp serve`, which is also what `ci-doctor mcp` runs."""

import os
from pathlib import Path

import typer
from typing_extensions import override

from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.context import files
from ci_doctor.config.files import ConfigFiles
from ci_doctor.config.loader import REPO_CONFIG
from ci_doctor.errors import SetupError

PROJECT_HELP = "The project to serve. Default: the current directory, where most agents start the server."


class ServeCommand(Command):
    """Serve ci-doctor to a coding agent over MCP, on stdio.

    The agent is the model: it gets each failed job's evidence and explains it, and
    can pick matchers for the project. Matcher edits go to `./.ci-doctor.yml` unless
    `--write-to` names another file.
    """

    name = "serve"

    @override
    def run(self, project: Path | None = typer.Option(None, "--project", help=PROJECT_HELP)) -> None:
        from ci_doctor.mcp.server import build  # the SDK is only worth importing for this command

        given = files()
        # `-f` and `--write-to` are relative to where the agent started us, so fix them first.
        read = [p.resolve() for p in given.files]
        write = given.write_to.resolve() if given.write_to else REPO_CONFIG  # REPO_CONFIG: the project's
        if project is not None:
            if not project.is_dir():
                raise SetupError(f"--project {project}: no such directory")
            os.chdir(project)
        build(ConfigFiles(read, write)).run("stdio")
