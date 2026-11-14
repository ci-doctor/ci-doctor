"""`ci-doctor mcp`: the tools an agent calls, driven through the SDK's in-memory client.

No socket and no subprocess: the client and the server share a pair of memory
streams, so the air-gap guard in `conftest.py` stays on.
"""

import json
import subprocess
import sys

import anyio
import pytest
import yaml
from mcp import Client

from ci_doctor.config.files import ConfigFiles
from ci_doctor.core.models import Job, Run
from ci_doctor.mcp.server import build
from tests import support

SECRET = "glpat-" + "a" * 20
PYTEST_FAILURE = [
    "===== FAILURES =====",
    "E   assert 1 == 2",
    f"pushing with {SECRET}",
    "===== short test summary =====",
]


@pytest.fixture
def project(tmp_path, monkeypatch, fx_registry):
    """A project directory as the cwd, its `.ci-doctor.yml` selecting `fx/pytest`."""
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".ci-doctor.yml"
    config.write_text(
        yaml.safe_dump({"matchers": {"registries": {"fx": str(fx_registry)}, "include": ["fx/pytest"]}})
    )
    return config


def _call(config, tool, **arguments):
    """Call one tool on a fresh server whose matcher edits go to `config`; returns the raw result."""

    async def call():
        async with Client(build(ConfigFiles([], config))) as client:
            return await client.call_tool(tool, arguments)

    return anyio.run(call)


def _result(config, tool, **arguments):
    """The tool's structured result, unwrapped; fails the test on a tool error."""
    result = _call(config, tool, **arguments)
    assert not result.is_error, result.content
    content = result.structured_content
    return content["result"] if set(content) == {"result"} else content


def _error(config, tool, **arguments):
    """The message of a call that must fail."""
    result = _call(config, tool, **arguments)
    assert result.is_error
    return result.content[0].text


def _wire(monkeypatch, project, provider="github", names="ab"):
    """Serve a run whose jobs all failed with `PYTEST_FAILURE`, framed as `provider` frames its logs."""
    project.write_text(project.read_text() + f"ci: {provider}\n")

    class Provider:
        def fetch_run(self, run_ref):
            jobs = [
                Job(id=n, name=n, status="failed", started_at=f"2024-01-01T00:0{i}:00Z")
                for i, n in enumerate(names)
            ]
            return Run(id=run_ref, jobs=jobs)

        def fetch_job_log(self, job):
            return support.script_log(provider, PYTEST_FAILURE)

    monkeypatch.setattr("ci_doctor.service.analyze.make_ci_provider", lambda cfg: Provider())


@pytest.mark.parametrize("provider", support.PROVIDERS)
def test_analyze_run_briefs_the_job_for_the_agent_to_explain(project, monkeypatch, provider):
    """The brief carries the settled phase, the excerpt, the matcher behind it and the instructions."""
    _wire(monkeypatch, project, provider, names="a")
    [brief] = _result(project, "analyze_run", run_id="42")
    assert brief["failure_phase"] == "script"
    assert "E   assert 1 == 2" in brief["excerpt"]
    assert [m["id"] for m in brief["matchers"]] == ["fx/pytest"]
    assert "Do NOT dispute" in brief["instructions"]


def test_a_brief_never_carries_an_unredacted_secret(project, monkeypatch):
    """Invariant #5: the excerpt leaves the process scrubbed."""
    _wire(monkeypatch, project, names="a")
    result = _call(project, "analyze_run", run_id="42")
    assert SECRET not in json.dumps(result.structured_content)
    assert "[REDACTED:" in result.structured_content["result"][0]["excerpt"]


def test_analysis_calls_no_model_even_when_one_is_configured(project, monkeypatch):
    """Invariant #12: the agent is the model, whatever `llm:` says."""
    _wire(monkeypatch, project, names="a")
    project.write_text(project.read_text() + "llm: {enabled: true, model: m, api_base: 'http://stub'}\n")

    def no_client(*args, **kwargs):
        raise AssertionError("the MCP server built an LLM client")

    monkeypatch.setattr("ci_doctor.llm.backends.make_client", no_client)
    assert _result(project, "analyze_run", run_id="42")


def test_analyze_run_briefs_each_failed_job_and_writes_nothing(project, tmp_path, monkeypatch):
    """A live run: one brief per failed job, in pipeline order, and no report files (invariant #10)."""
    _wire(monkeypatch, project)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert [b["job_name"] for b in _result(project, "analyze_run", run_id="42")] == ["a", "b"]
    assert [b["job_id"] for b in _result(project, "analyze_run", run_id="42", job_id="b")] == ["b"]
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_failure_reaches_the_agent_as_a_message_and_the_server_survives(project, monkeypatch):
    """A crash comes back readable and redacted; it does not take the server down."""

    def crash(cfg):
        raise ConnectionError(f"401 from the CI system, token {SECRET}")

    monkeypatch.setattr("ci_doctor.service.analyze.make_ci_provider", crash)

    async def calls():
        async with Client(build(ConfigFiles([], project))) as client:
            crashed = await client.call_tool("analyze_run", {"run_id": "42"})
            alive = await client.call_tool("check_setup", {})
            return crashed, alive

    crashed, alive = anyio.run(calls)
    assert crashed.is_error and "ConnectionError: 401 from the CI system" in crashed.content[0].text
    assert SECRET not in crashed.content[0].text
    assert not alive.is_error and "config ok" in alive.content[0].text


def test_an_agent_can_find_add_list_and_remove_matchers(project):
    """The selection flow end to end: search, add, see it listed, remove it again."""
    hits = {h["name"]: h for h in _result(project, "search_matchers", registry="fx")}
    assert hits["fx/python_traceback"]["kind"] == "matcher" and hits["fx/python"]["kind"] == "bundle"
    assert [h["name"] for h in _result(project, "search_matchers", term="uncaught", registry="fx")] == [
        "fx/python_traceback"
    ]

    added = _result(project, "add_matchers", refs=["fx/python_traceback", "fx/pytest"])
    assert added["file"] == str(project) and added["changed"] == ["fx/python_traceback"]
    assert added["skipped"] == [{"ref": "fx/pytest", "why": "already in include"}]
    listed = _result(project, "list_matchers")
    assert [m["matcher"] for m in listed["included"]] == ["fx/pytest", "fx/python_traceback"]

    assert _result(project, "remove_matchers", refs=["fx/python_traceback"])["changed"] == [
        "fx/python_traceback"
    ]
    assert yaml.safe_load(project.read_text())["matchers"]["include"] == ["fx/pytest"]


def test_add_edits_only_the_matchers_block_and_rejects_an_unknown_name(project):
    """Invariant #12: the one thing the server writes is `matchers:`; a bad ref writes nothing."""
    project.write_text("# the team's config\nci: gitlab\n" + project.read_text())
    before = project.read_text()
    assert "did you mean fx/pytest?" in _error(project, "add_matchers", refs=["fx/pytst"])
    assert project.read_text() == before

    _result(project, "add_matchers", refs=["fx/python_traceback"])
    after = project.read_text()
    assert after.startswith("# the team's config\nci: gitlab\n")
    assert yaml.safe_load(after) == {**yaml.safe_load(before), "matchers": yaml.safe_load(after)["matchers"]}


def test_tools_say_which_of_them_write():
    """A harness asks the user before a tool that is not read-only: only the two edits are."""

    async def tools():
        async with Client(build(ConfigFiles([]))) as client:
            return (await client.list_tools()).tools

    writes = {t.name for t in anyio.run(tools) if not t.annotations.read_only_hint}
    assert writes == {"add_matchers", "remove_matchers"}


def test_stdout_carries_only_protocol(project):
    """Invariant #12: on stdio, a stray print would corrupt the stream the agent is parsing."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    proc = subprocess.run(
        [sys.executable, "-m", "ci_doctor.cli", "--verbose", "mcp"],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert replies and replies[0]["result"]["serverInfo"]["name"] == "ci-doctor"
