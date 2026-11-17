"""`ci-doctor agent install`."""

import typer
from typing_extensions import override

from ci_doctor import __version__
from ci_doctor.cli._lib.command import Command
from ci_doctor.cli._lib.outputs.changes import confirm, preview, show
from ci_doctor.cli._lib.outputs.message import say
from ci_doctor.cli._lib.tui import (
    Cancelled,
    Option,
    Prompter,
    RichPrompter,
    interactive,
    pick_many,
    pick_one,
    yes_no,
)
from ci_doctor.harness.entry import on_path
from ci_doctor.harness.install import install
from ci_doctor.harness.registry import TARGETS, found, select
from ci_doctor.harness.targets.base import Location, Target


class InstallCommand(Command):
    """Connect coding agents to ci-doctor's MCP server: Claude Code, Cursor, Codex, and more.

    Writes each agent's MCP config, a short instructions block, and (Claude Code only)
    an allow list for the read-only tools. Running it again changes nothing.
    `--dry-run` shows each file and the config that would go in it, and writes nothing.
    With no flags, in a terminal, it asks which agents and where, then asks before writing.
    With flags, or from a script, it writes without asking.
    """

    name = "install"
    examples = (
        "ci-doctor agent install   # asks which agents and where; from a script, the agents found here",
        "ci-doctor agent install --target claude,cursor --location global",
        "ci-doctor agent install --target codex --location global --dry-run",
    )

    @override
    def run(
        self,
        target: str | None = typer.Option(
            None,
            "--target",
            "-t",
            help=f"auto (the agents found here, Claude Code if none), all, or ids: {','.join(TARGETS)}.",
        ),
        location: Location | None = typer.Option(
            None, "--location", "-l", help="local (the default): this project's files. global: your own."
        ),
        permissions: bool | None = typer.Option(
            None,
            "--permissions/--no-permissions",
            help="Auto-allow the read-only tools in Claude Code (the default).",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show the files and the config that would be added; write nothing."
        ),
    ) -> None:
        no_flags = target is None and location is None and permissions is None and not dry_run
        if no_flags and interactive():
            self._ask()
            return
        agents = select(target) if target else found()
        location = location or Location.LOCAL
        if dry_run:
            preview(agents, location)
            return
        # Flags, or a script: the decision is made, so list the files and write them.
        confirm(agents, location, True, "add ci-doctor to")
        self._install(agents, location, permissions=permissions is not False)

    def _ask(self) -> None:
        """No flags in a terminal: ask which agents, where, and whether to auto-allow; then install."""
        prompter = RichPrompter()
        try:
            prompter.show(f"ci-doctor {__version__} · connect coding agents", "bold")
            agents = self._agents(prompter)
            if not agents:
                say("No agent selected; nothing written.")
                return
            location = self._location(prompter, agents)
            permissions = self._permissions(prompter, agents)
            prompter.show("")
            confirm(agents, location, True, "add ci-doctor to")  # lists the files
            if not yes_no(prompter, "Go ahead?"):
                say("Nothing written.")
                return
        except Cancelled:
            say("Cancelled; nothing written.")
            return
        self._install(agents, location, permissions=permissions)

    def _agents(self, prompter: Prompter) -> list[Target]:
        """Every agent, the ones found here ticked (Claude Code when none is)."""
        targets = list(TARGETS.values())
        here = found()
        ticked = [i for i, t in enumerate(targets) if t in here]
        options = [Option(t, t.name, self._hint(t, t.detected())) for t in targets]
        return pick_many(prompter, "Which agents should ci-doctor configure?", options, ticked)

    @staticmethod
    def _hint(target: Target, detected: bool) -> str:
        """`detected` or `not found`, then `global only` and what else the agent needs."""
        parts = ["detected" if detected else "not found"]
        if Location.LOCAL not in target.locations:
            parts.append("global only")
        if target.requires:
            parts.append(f"needs {target.requires.split(':')[0].removeprefix('the ')}")
        return " · ".join(parts)

    @staticmethod
    def _location(prompter: Prompter, agents: list[Target]) -> Location:
        """This project (the default) or every project; not asked when no agent has a project config."""
        if all(Location.LOCAL not in a.locations for a in agents):
            prompter.show("Writing your own config: these agents have no project config.", "dim")
            return Location.GLOBAL
        options = [
            Option(Location.LOCAL, "Just this project", "./.mcp.json, ./AGENTS.md, ..."),
            Option(Location.GLOBAL, "All my projects", "~/.claude.json, ~/.codex, ..."),
        ]
        return pick_one(prompter, "Where?", options)

    @staticmethod
    def _permissions(prompter: Prompter, agents: list[Target]) -> bool:
        """Asked only when Claude Code is picked: it is the one agent with an allow list."""
        if not any(a.id == "claude" for a in agents):
            return True
        return yes_no(prompter, "Auto-allow ci-doctor's read-only tools in Claude Code?")

    @staticmethod
    def _install(agents: list[Target], location: Location, *, permissions: bool) -> None:
        """Write each agent's config, report every file, then say what is left to do."""
        show(install(agents, location, permissions=permissions), location)
        if not on_path():
            say(
                "Warning: `ci-doctor` is not on your PATH, so agents cannot start it. "
                "Install it with `uv tool install ci-doctorr` or `pipx install ci-doctorr`.",
                "yellow",
            )
        say(
            "Restart your agents to load ci-doctor. To analyze live runs, set CI_DOCTOR_GITHUB_TOKEN "
            "or CI_DOCTOR_GITLAB_TOKEN where the agent runs."
        )
