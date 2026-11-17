"""`ci-doctor agent install` and `uninstall`, for every agent, in a throwaway home and project.

`HOME` and the agents' own variables point into `tmp_path`, and `PATH` is empty so no
real agent is detected. Nothing here touches the developer's own configs.
"""

import json
import sys
import tomllib

import anyio
import pytest
import yaml
from mcp import Client

from ci_doctor import cli
from ci_doctor.config.files import ConfigFiles
from ci_doctor.errors import SetupError
from ci_doctor.harness.entry import READ_ONLY_TOOLS, SERVER
from ci_doctor.harness.install import install, uninstall
from ci_doctor.harness.registry import TARGETS, select
from ci_doctor.mcp.server import build

CASES = [(t.id, loc) for t in TARGETS.values() for loc in sorted(t.locations)]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home and an empty project as the cwd; returns the home."""
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    for var in ("XDG_CONFIG_HOME", "HERMES_HOME", "CODEX_HOME", "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(project)
    return home


def _seed(path):
    """Put unrelated content in a file the agent target will edit, the way a user's file looks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in (".json", ".jsonc"):
        path.write_text(
            json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}, indent=2) + "\n"
        )
    elif path.suffix == ".toml":
        path.write_text('# my settings\nmodel = "o3"\n\n[mcp_servers.other]\ncommand = "x"\n')
    elif path.suffix == ".yaml":
        path.write_text(
            yaml.safe_dump({"model": "x", "mcp_servers": {"other": {"command": "x"}}}, sort_keys=False)
        )
    else:
        path.write_text("# Team notes\n\nRun the tests with uv.\n")


def _owned(path):
    """Files the target owns outright (Kiro's steering file) have nothing of the user's to keep."""
    return path.name == "ci-doctor.md"


@pytest.mark.parametrize(("agent", "location"), CASES)
def test_install_is_repeatable_and_uninstall_restores_every_file(home, agent, location):
    """Install, install again (no change), uninstall: a file in the agent's own style is back byte for byte."""
    target = TARGETS[agent]
    seeded = {p: _seed(p) or p.read_text() for p in target.paths(location) if not _owned(p)}

    first = target.install(location, permissions=True)
    assert all(c.action in ("created", "updated") for c in first), first
    assert target.configured(location)
    assert all(c.action == "unchanged" for c in target.install(location, permissions=True))

    target.uninstall(location)
    assert not target.configured(location)
    assert {p: p.read_text() for p in seeded} == seeded
    assert not any(_owned(p) and p.exists() for p in target.paths(location))
    assert all(c.action == "absent" for c in target.uninstall(location))


@pytest.mark.parametrize(("agent", "location"), CASES)
def test_install_into_nothing_creates_files_that_parse(home, agent, location):
    """A first install on a clean machine writes config the agent can read."""
    target = TARGETS[agent]
    target.install(location, permissions=True)
    for path in target.paths(location):
        text = path.read_text()
        if path.suffix in (".json", ".jsonc"):
            json.loads(text)
        elif path.suffix == ".toml":
            tomllib.loads(text)
        elif path.suffix == ".yaml":
            yaml.safe_load(text)
        else:
            assert "analyze_run" in text
    assert target.configured(location)


@pytest.mark.parametrize(("agent", "location"), CASES)
def test_a_snippet_writes_nothing(home, agent, location):
    """The snippet names its file and holds the server, and no file is created."""
    snippet = TARGETS[agent].snippet(location)
    assert snippet.startswith("# Add to ") and SERVER in snippet
    assert not any(home.rglob("*")) and not any(home.parent.joinpath("project").rglob("*"))


def test_claude_allows_only_the_read_only_tools(home):
    """The tools that edit `.ci-doctor.yml` still ask; the user's own allow entries stay."""
    settings = home.parent / "project" / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
    TARGETS["claude"].install("local", permissions=True)
    allow = json.loads(settings.read_text())["permissions"]["allow"]
    assert allow == ["Bash(ls)", *(f"mcp__{SERVER}__{tool}" for tool in READ_ONLY_TOOLS)]

    TARGETS["claude"].uninstall("local")
    assert json.loads(settings.read_text())["permissions"]["allow"] == ["Bash(ls)"]


def test_no_permissions_writes_no_allow_list(home):
    """`--no-permissions` leaves Claude's settings alone."""
    TARGETS["claude"].install("local", permissions=False)
    assert not (home.parent / "project" / ".claude" / "settings.json").exists()


def test_the_allow_list_matches_the_servers_read_only_tools():
    """The tools ci-doctor auto-allows are exactly the ones the server marks read-only."""

    async def tools():
        async with Client(build(ConfigFiles([]))) as client:
            return (await client.list_tools()).tools

    read_only = {t.name for t in anyio.run(tools) if t.annotations.read_only_hint}
    assert read_only == set(READ_ONLY_TOOLS)


def test_cursor_is_told_which_project_to_serve(home):
    """Cursor starts servers outside the project, so both configs name it, with no path from this machine.

    A project's `.cursor/mcp.json` is committed: a teammate's Cursor must not get `/home/<you>/...`.
    """
    cursor = TARGETS["cursor"]
    for location in ("local", "global"):
        assert cursor.entry(location)["args"] == ["mcp", "--project", "${workspaceFolder}"]
    cursor.install("local", permissions=True)
    assert str(home) not in (home.parent / "project" / ".cursor" / "mcp.json").read_text()


def test_codex_keeps_the_users_env_table_across_reinstalls(home):
    """A token the user put in `[mcp_servers.ci-doctor.env]` survives install; uninstall removes it with ours."""
    config = home / ".codex" / "config.toml"
    TARGETS["codex"].install("global", permissions=True)
    config.write_text(config.read_text() + '\n[mcp_servers.ci-doctor.env]\nCI_DOCTOR_GITHUB_TOKEN = "t"\n')
    TARGETS["codex"].install("global", permissions=True)
    assert tomllib.loads(config.read_text())["mcp_servers"][SERVER]["env"] == {"CI_DOCTOR_GITHUB_TOKEN": "t"}

    TARGETS["codex"].uninstall("global")
    assert not config.exists()  # it held only ci-doctor's tables


def test_hermes_joins_a_cli_toolset_list_when_there_is_one(home):
    """A Hermes profile that lists its toolsets would hide ours, so it is added there and removed later."""
    config = home / ".hermes" / "config.yaml"
    config.parent.mkdir()
    config.write_text(yaml.safe_dump({"platform_toolsets": {"cli": ["hermes-cli"]}}))
    TARGETS["hermes"].install("global", permissions=True)
    assert yaml.safe_load(config.read_text())["platform_toolsets"]["cli"] == ["hermes-cli", "mcp-ci-doctor"]
    TARGETS["hermes"].uninstall("global")
    assert yaml.safe_load(config.read_text()) == {"platform_toolsets": {"cli": ["hermes-cli"]}}


@pytest.mark.parametrize(
    ("agent", "location", "file", "text", "reason"),
    [
        ("claude", "local", ".mcp.json", "{not json", "is not plain JSON"),
        ("opencode", "local", "opencode.jsonc", '{\n  // mine\n  "theme": "x"\n}\n', "files with comments"),
        (
            "hermes",
            "global",
            "~/.hermes/config.yaml",
            "mcp_servers: {other: {command: x}}\n",
            "written inline",
        ),
        ("codex", "global", "~/.codex/config.toml", "model = \n", "is not valid TOML"),
    ],
)
def test_a_file_that_cannot_be_edited_safely_is_left_alone(home, agent, location, file, text, reason):
    """Unparseable, or with comments a rewrite would lose: skipped and reported, never clobbered."""
    path = home / file[2:] if file.startswith("~/") else home.parent / "project" / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    [outcome] = install([TARGETS[agent]], location, permissions=True)
    [change] = [c for c in outcome.changes if c.path == path]
    assert change.action == "skipped" and reason in change.note
    assert path.read_text() == text


def test_a_global_only_agent_is_skipped_for_local(home):
    """Codex has no project config; asking for one says so instead of writing somewhere wrong."""
    [outcome] = install([TARGETS["codex"]], "local", permissions=True)
    assert outcome.changes == [] and "use --location global" in outcome.skipped
    assert uninstall([TARGETS["codex"]], "local")[0].skipped


def test_auto_picks_the_agents_found_on_this_machine(home):
    """Detection looks for each agent's folder (and its command, here hidden by the empty PATH)."""
    assert select("auto") == []
    (home / ".cursor").mkdir()
    (home / ".codex").mkdir()
    assert [t.id for t in select("auto")] == ["cursor", "codex"]
    assert [t.id for t in select("codex, claude")] == ["claude", "codex"]
    with pytest.raises(SetupError, match="unknown agent 'vscode'"):
        select("vscode")


# --- the commands -------------------------------------------------------------


def _run(monkeypatch, capsys, *argv):
    """Run `ci-doctor --plain agent argv`; returns (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "--plain", "agent", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    out = capsys.readouterr()
    return exc.value.code, out.out, out.err


def test_install_writes_the_project_config_and_says_what_it_did(home, monkeypatch, capsys):
    """`agent install --target claude --yes` writes `.mcp.json` here and lists every file."""
    code, out, _ = _run(monkeypatch, capsys, "install", "--target", "claude", "--yes")
    assert code == 0
    assert json.loads((home.parent / "project" / ".mcp.json").read_text())["mcpServers"][SERVER]["args"] == [
        "mcp"
    ]
    assert "created ./.mcp.json" in out and "Restart your agents" in out

    code, out, _ = _run(monkeypatch, capsys, "uninstall", "--target", "claude", "--yes")
    assert code == 0 and "removed ./.mcp.json" in out


def test_dry_run_shows_the_files_and_config_and_writes_nothing(home, monkeypatch, capsys):
    """`--dry-run` names each file and prints the config to add; no file appears."""
    code, out, _ = _run(
        monkeypatch, capsys, "install", "--target", "codex,claude", "--location", "global", "--dry-run"
    )
    assert code == 0 and "Dry run, nothing is written" in out
    assert "~/.codex/config.toml" in out and "[mcp_servers.ci-doctor]" in out
    assert "~/.claude.json" in out and '"mcpServers"' in out
    assert not any(home.rglob("*")) and not any(home.parent.joinpath("project").rglob("*"))


def test_dry_run_says_which_agents_have_no_config_there(home, monkeypatch, capsys):
    """A global-only agent asked for a project install is shown as skipped, with the fix."""
    code, out, _ = _run(monkeypatch, capsys, "install", "--target", "codex", "--dry-run")
    assert code == 0 and "skipped: no local config; use --location global" in out
    assert not any(home.parent.joinpath("project").rglob("*"))


def test_an_unknown_target_is_a_usage_error(home, monkeypatch, capsys):
    """A typo in `--target` names the choices instead of crashing."""
    code, _, err = _run(monkeypatch, capsys, "install", "--target", "vscode", "--dry-run")
    assert code == 1 and "claude" in err and "internal error" not in err


def test_without_a_terminal_install_asks_for_yes(home, monkeypatch, capsys):
    """Nothing is written without a yes: from a script, that means `--yes`."""
    code, _, err = _run(monkeypatch, capsys, "install", "--target", "claude")
    assert code == 1 and "pass --yes" in err
    assert not (home.parent / "project" / ".mcp.json").exists()


def test_a_skipped_file_prints_the_snippet_and_exits_1(home, monkeypatch, capsys):
    """The rest is done; the file left alone gets the config to add by hand, and the exit says so."""
    (home.parent / "project" / ".mcp.json").write_text("{not json")
    code, out, err = _run(monkeypatch, capsys, "install", "--target", "claude", "--yes")
    assert code == 1 and "left as they were" in err
    assert "Add this by hand" in out and '"ci-doctor"' in out
    assert (home.parent / "project" / ".claude" / "CLAUDE.md").exists()  # the rest was still done


def test_serving_a_project_that_does_not_exist_fails(home, monkeypatch, capsys):
    """`ci-doctor mcp --project` must name a folder."""
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "--plain", "mcp", "--project", "nowhere"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1 and "no such directory" in capsys.readouterr().err


HERMES_WITH_COMMENTS = """\
# Hermes settings
model: gpt-x  # the default model

mcp_servers:
    # code search
    other:
        command: x

# toolsets for the CLI
platform_toolsets:
    cli:
    - hermes-cli
"""


def test_hermes_keeps_comments_and_its_own_indent(home):
    """Edited as text: comments and 4-space indent stay, and uninstall gives back the same bytes."""
    config = home / ".hermes" / "config.yaml"
    config.parent.mkdir()
    config.write_text(HERMES_WITH_COMMENTS)
    [change] = TARGETS["hermes"].install("global", permissions=True)
    assert change.action == "updated"
    text = config.read_text()
    assert "# code search" in text and "# toolsets for the CLI" in text and "    ci-doctor:" in text
    data = yaml.safe_load(text)
    assert data["mcp_servers"][SERVER]["args"] == ["mcp"]
    assert data["platform_toolsets"]["cli"] == ["hermes-cli", "mcp-ci-doctor"]

    TARGETS["hermes"].uninstall("global")
    assert config.read_text() == HERMES_WITH_COMMENTS


@pytest.mark.parametrize(("agent", "location"), CASES)
def test_a_clean_machine_keeps_nothing_of_ci_doctor_after_uninstall(home, agent, location):
    """Instructions, TOML and YAML files left empty are deleted; a JSON config stays, holding nothing of ours."""
    TARGETS[agent].install(location, permissions=True)
    TARGETS[agent].uninstall(location)
    for path in (p for p in home.parent.rglob("*") if p.is_file()):
        assert path.suffix in (".json", ".jsonc"), path
        text = path.read_text()
        assert SERVER not in text, path
        assert json.loads(text) in ({}, {"$schema": "https://opencode.ai/config.json"}), path


def test_uninstall_lists_only_the_agents_that_have_ci_doctor(home, monkeypatch, capsys):
    """With nothing installed it says so; after one install it names only that agent."""
    code, out, _ = _run(monkeypatch, capsys, "uninstall", "--yes")
    assert code == 0 and "nothing to remove" in out

    _run(monkeypatch, capsys, "install", "--target", "gemini", "--yes")
    code, out, _ = _run(monkeypatch, capsys, "uninstall", "--yes")
    assert code == 0 and "Gemini CLI" in out and "Claude Code" not in out and "Cursor" not in out


def test_leftover_instructions_alone_still_count_as_installed(home):
    """An instructions block whose server entry was removed by hand is still found and removed."""
    TARGETS["gemini"].install("local", permissions=True)
    (home.parent / "project" / ".gemini" / "settings.json").unlink()
    assert TARGETS["gemini"].present("local")
    TARGETS["gemini"].uninstall("local")
    assert not TARGETS["gemini"].present("local")


@pytest.mark.parametrize(
    ("agent", "location", "file", "text", "after"),
    [
        ("antigravity", "global", "~/.gemini/antigravity/mcp_config.json", '{\n  "mcpServers": {}\n}\n', {}),
        ("antigravity", "global", "~/.gemini/antigravity/mcp_config.json", '{\n  "mcpServers": {}\n}', {}),
        ("claude", "local", ".claude/settings.json", "{}\n", {}),
        ("cursor", "local", ".cursor/mcp.json", "{}\n", {}),
        ("gemini", "global", "~/.gemini/settings.json", '{\n    "theme": "dark"\n}\n', {"theme": "dark"}),
        ("cursor", "global", "~/.cursor/mcp.json", '{\n\t"mcpServers": {}\n}\n', {}),
        ("cursor", "global", "~/.cursor/mcp.json", '{"mcpServers":{}}', {}),
        (
            "gemini",
            "global",
            "~/.gemini/settings.json",
            '{"theme": "dark", "mcpServers": {}}\n',
            {"theme": "dark"},
        ),
    ],
)
def test_a_users_nearly_empty_json_file_survives_uninstall(home, agent, location, file, text, after):
    """Found on a real machine: Antigravity's own `{"mcpServers": {}}` was deleted.

    A JSON config is never deleted, since the agent manages it. Uninstall keeps the
    user's data and drops only the containers it leaves empty.
    """
    path = home / file[2:] if file.startswith("~/") else home.parent / "project" / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    TARGETS[agent].install(location, permissions=True)
    TARGETS[agent].uninstall(location)
    assert json.loads(path.read_text()) == after


def test_an_instructions_file_left_empty_is_deleted(home):
    """A GEMINI.md that held only ci-doctor's block has nothing of the user's left to keep."""
    notes = home / ".gemini" / "GEMINI.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("")
    TARGETS["gemini"].install("global", permissions=True)
    TARGETS["gemini"].uninstall("global")
    assert not notes.exists()


def test_uninstall_keeps_the_users_own_entries_in_a_container(home):
    """Only ci-doctor's entry goes: `mcpServers` stays while the user's server is in it."""
    mcp = home.parent / "project" / ".mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}) + "\n")
    TARGETS["claude"].install("local", permissions=True)
    TARGETS["claude"].uninstall("local")
    assert json.loads(mcp.read_text()) == {"mcpServers": {"other": {"command": "x"}}}


def test_install_warns_when_agents_cannot_find_ci_doctor(home, monkeypatch, capsys):
    """Agents start the server by name; if `ci-doctor` is not on PATH, say so and how to fix it."""
    code, out, _ = _run(monkeypatch, capsys, "install", "--target", "claude", "--yes")
    assert code == 0 and "`ci-doctor` is not on your PATH" in out and "uv tool install ci-doctorr" in out

    bin_dir = home / "bin"
    bin_dir.mkdir()
    (bin_dir / "ci-doctor").write_text("#!/bin/sh\n")
    (bin_dir / "ci-doctor").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    code, out, _ = _run(monkeypatch, capsys, "install", "--target", "claude", "--yes")
    assert code == 0 and "not on your PATH" not in out


def test_antigravity_uninstall_finds_an_entry_left_in_its_old_config(home, monkeypatch, capsys):
    """Install, then Antigravity moves to its new config: uninstall must still find the old entry."""
    (home / ".gemini" / "antigravity").mkdir(parents=True)
    TARGETS["antigravity"].install("global", permissions=True)
    (home / ".gemini" / "config").mkdir()
    (home / ".gemini" / "config" / ".migrated").touch()
    assert TARGETS["antigravity"].present("global")

    code, out, _ = _run(monkeypatch, capsys, "uninstall", "--location", "global", "--yes")
    assert code == 0 and "Antigravity" in out
    assert not TARGETS["antigravity"].present("global")


def test_pi_install_names_the_extension_it_needs(home, monkeypatch, capsys):
    """Pi has no MCP of its own: the server goes in its `mcp.json`, and install says to add the adapter."""
    code, out, _ = _run(monkeypatch, capsys, "install", "--target", "pi", "--location", "global", "--yes")
    config = json.loads((home / ".pi" / "agent" / "mcp.json").read_text())
    assert code == 0 and config["mcpServers"][SERVER] == {"command": "ci-doctor", "args": ["mcp"]}
    assert "analyze_run" in (home / ".pi" / "agent" / "AGENTS.md").read_text()
    assert "pi install npm:pi-mcp-adapter" in out


def test_pi_follows_its_agent_dir(home, monkeypatch):
    """`PI_CODING_AGENT_DIR` moves pi's whole agent folder, and the config moves with it."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home / "elsewhere"))
    TARGETS["pi"].install("global", permissions=True)
    assert (home / "elsewhere" / "mcp.json").exists() and not (home / ".pi").exists()
