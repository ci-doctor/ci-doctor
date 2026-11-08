"""`ci-doctor report`: what a bug report needs, and nothing a stranger shouldn't read."""

import sys

import pytest

from ci_doctor import __version__, cli
from ci_doctor.service.report import build


def _fields(sections):
    return {s.title: dict(s.fields) for s in sections}


def test_environment_values_never_appear(monkeypatch):
    """A proxy URL or a CA path carries hosts and usernames: only set/unset is reported."""
    monkeypatch.setenv("HTTPS_PROXY", "http://user:hunter2@proxy.corp:3128")
    monkeypatch.setenv("CI_DOCTOR_GITLAB_TOKEN", "glpat-secret")
    report = _fields(build([]))
    assert report["environment"]["HTTPS_PROXY"] == "set"
    assert report["environment"]["CI_DOCTOR_*"] == "CI_DOCTOR_GITLAB_TOKEN"
    text = repr(report)
    assert "hunter2" not in text and "glpat-secret" not in text


def test_config_is_named_not_shown(tmp_path, monkeypatch):
    """The file's name, the backend and model; not its path or the endpoint."""
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "team.yml"
    cfg.write_text("llm:\n  model: qwen\n  api_base: http://gpu.internal:8000/v1\n")
    config = _fields(build([cfg]))["config"]
    assert config["-f files"] == "team.yml"
    assert config["llm"] == "openai, model qwen"
    assert config["llm api_base"] == "set"
    assert str(tmp_path) not in repr(config) and "gpu.internal" not in repr(config)


def test_an_invalid_config_is_reported_not_raised(tmp_path):
    """The report is how a broken setup gets diagnosed, so it can't fail on one."""
    cfg = tmp_path / "bad.yml"
    cfg.write_text("nope: 1\n")
    assert "error" in _fields(build([cfg]))["config"]


def test_the_command_prints_every_section(monkeypatch, capsys):
    """`ci-doctor --plain report` prints one `key: value` line per field and exits 0."""
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "--plain", "report"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    out = capsys.readouterr().out
    assert exc.value.code == 0
    assert f"  version: {__version__}" in out
    assert {"ci-doctor", "packages", "config", "environment"} <= set(out.splitlines())
