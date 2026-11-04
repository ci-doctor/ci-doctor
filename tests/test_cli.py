"""CLI surface: the exit-0 invariant, log levels, replay, and `config`."""

import json
import sys

import pytest

from ci_doctor import cli
from ci_doctor.cli import _output
from tests import support


def _run(monkeypatch, capsys, *argv):
    """Invoke the CLI with argv and return stdout.

    Args:
        monkeypatch: pytest fixture, used to swap sys.argv.
        capsys: pytest fixture capturing stdout.
        *argv: Arguments after the program name.

    Returns:
        Captured stdout.
    """
    monkeypatch.setattr(sys, "argv", ["ci-doctor", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    return capsys.readouterr().out


@pytest.fixture
def registry_config(tmp_path, monkeypatch, fx_registry):
    """Run in tmp_path with a `.ci-doctor.yml` selecting the test registry's matchers."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci-doctor.yml").write_text(
        f"matchers:\n  registries:\n    fx: {fx_registry}\n  include: [fx/all]\n"
    )
    return tmp_path


def _job_log(where, provider):
    """Write a failed pytest job's log in `provider`'s format, for offline replay."""
    path = where / "job.log"
    path.write_text(support.script_log(provider, support.FAIL))
    return str(path)


def test_main_always_exits_zero_on_error(monkeypatch):
    """Invariant #3: a crash once analysis runs exits 0, so a pipeline outcome never changes."""

    def crash():
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "app", crash)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["-f", "/no/such/config.yml", "analyze", "123"],  # a config that isn't there
        ["analyze", "no/such/job.log"],  # a log that isn't there
        ["analyze"],  # nothing to do
        ["analyze", "--no-such-flag"],  # bad usage
        ["analyze", "123"],  # no matchers selected
    ],
)
def test_a_broken_setup_exits_one(argv, monkeypatch, capsys, tmp_path):
    """Invariant #3: every way the setup can be wrong is seen, before analysis starts."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ci-doctor", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_an_empty_include_says_how_to_pick_matchers(monkeypatch, capsys, tmp_path):
    """The zero-config run fails with the next step, not a stack of nothing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "123"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "ci-doctor matchers add official/<name>" in capsys.readouterr().err


def test_a_setup_error_is_the_one_exit_one(monkeypatch, capsys):
    """Invariant #3: a broken setup exits 1 and says why; nothing else does."""

    def broken_setup():
        raise cli.SetupError("registry 'acme': index.yaml is out of date")

    monkeypatch.setattr(cli, "app", broken_setup)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "index.yaml is out of date" in capsys.readouterr().err


def test_verbose_enables_debug_logging():
    """`--verbose` turns on DEBUG; without it the level returns to INFO."""
    import logging

    _output.configure_logging(True)
    assert logging.getLogger("ci_doctor").level == logging.DEBUG
    _output.configure_logging(False)  # default INFO when not verbose and no env override
    assert logging.getLogger("ci_doctor").level == logging.INFO


@pytest.mark.parametrize("provider", support.PROVIDERS)
def test_from_file_replay_smoke(provider, monkeypatch, capsys, registry_config):
    """Offline replay renders a report and writes both artifacts."""
    tmp_path = registry_config  # report.md/report.json land here, not in the repo
    log = _job_log(tmp_path, provider)
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "--no-color", "analyze", log])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Root cause" in out
    assert "script" in out
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()


@pytest.mark.parametrize("provider", support.PROVIDERS)
def test_format_json_puts_a_parseable_report_on_stdout(provider, monkeypatch, capsys, registry_config):
    """`--format json` is what an agent reads: stdout must parse, with no panels in it."""
    tmp_path = registry_config
    log = _job_log(tmp_path, provider)
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "analyze", "--format", "json", log])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0

    captured = capsys.readouterr()
    reports = json.loads(captured.out)  # the whole of stdout, not a fragment of it
    assert reports and {"summary", "failure_phase", "handoff_prompt"} <= reports[0].keys()
    assert "Root cause" not in captured.out  # the rendered panels are suppressed
    # The artifacts still land, and the human-facing chatter stays on stderr so it
    # can never end up inside what the caller is parsing.
    assert (tmp_path / "report.json").exists()
    assert "wrote report.md" in captured.err


def test_format_json_stdout_has_nothing_but_json(registry_config):
    """`--format json | jq` must work, so nothing may share stdout with the payload.

    A subprocess, not an in-process call: pytest's logging plugin owns the root
    logger, so `basicConfig` in `main()` no-ops and the rich handler never writes
    during a normal test. Only a real process shows which stream the logs land on.
    """
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ci_doctor.cli",
            "analyze",
            "--format",
            "json",
            _job_log(registry_config, support.PROVIDERS[0]),
        ],
        cwd=registry_config,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0  # invariant #3, even here
    json.loads(proc.stdout)  # raises if a log line leaked onto stdout
    assert "analyzing" in proc.stderr  # ...because the diagnostics went to stderr


def test_config_schema_is_valid_json(monkeypatch, capsys, tmp_path):
    """`--schema` emits parseable JSON Schema carrying the published $id."""
    monkeypatch.chdir(tmp_path)  # no stray .ci-doctor.yml from the repo root
    schema = json.loads(_run(monkeypatch, capsys, "config", "--schema"))
    assert schema["$id"].endswith("ci-doctor.schema.json")
    assert "extraction" in schema["properties"]


def test_config_prints_the_effective_config(monkeypatch, capsys, tmp_path):
    """The bare command shows the user's layer merged onto the defaults."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci-doctor.yml").write_text("llm:\n  model: my-model\n")
    out = _run(monkeypatch, capsys, "--no-color", "config")
    assert "my-model" in out  # the user layer
    assert "tail_lines" in out  # and the untouched defaults


def test_config_diff_shows_only_what_the_user_changed(monkeypatch, capsys, tmp_path):
    """`--diff` shows the replaced default and the new value, nothing untouched."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci-doctor.yml").write_text("llm:\n  model: my-model\n")
    out = _run(monkeypatch, capsys, "--no-color", "config", "--diff")
    assert "-  model: null" in out
    assert "+  model: my-model" in out
    assert "tail_lines" not in out  # unchanged keys stay out of the diff


def test_config_diff_is_empty_on_stock_defaults(monkeypatch, capsys, tmp_path):
    """With no user config, `--diff` says so instead of printing an empty diff."""
    monkeypatch.chdir(tmp_path)
    assert "no differences" in _run(monkeypatch, capsys, "--no-color", "config", "--diff")


def test_repeated_config_flags_apply_left_to_right(monkeypatch, capsys, tmp_path):
    """Several -f files stack, and the rightmost wins on a key they both set."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.yml").write_text("llm:\n  model: from-a\n  temperature: 0.9\n")
    (tmp_path / "b.yml").write_text("llm:\n  model: from-b\n")
    out = _run(monkeypatch, capsys, "--no-color", "-f", "a.yml", "-f", "b.yml", "config")
    assert "model: from-b" in out  # the rightmost file wins
    assert "temperature: 0.9" in out  # keys only the earlier file set survive


def test_config_validate_reports_ok_and_failure(monkeypatch, capsys, registry_config):
    """`--validate` is the dry run: a bad key exits 1 naming it; a good setup counts its matchers."""
    (registry_config / "bad.yml").write_text("llm:\n  nope: 1\n")
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "-f", "bad.yml", "config", "--validate"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "invalid config" in capsys.readouterr().err  # named, not raised

    out = _run(monkeypatch, capsys, "config", "--validate")
    assert "config ok" in out
    assert "2 matchers from 1 registries" in out


def test_analyze_reports_a_bad_config_as_a_config_error(monkeypatch, capsys, tmp_path):
    """A rejected regex is the user's config, not an internal error."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.yml").write_text("denoise:\n  noise_patterns: ['(?=x)']\n")
    (tmp_path / "job.log").write_text("boom\n")
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "-f", "bad.yml", "analyze", "job.log"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.value.code == 1
    assert "invalid config" in err
    assert "internal error" not in err


def test_the_config_pager_ignores_manpager_and_keeps_colours(monkeypatch):
    """Pydoc prefers $MANPAGER; a `col -bx` one ate the escapes. Ours runs $PAGER with less -R."""
    seen = {}
    monkeypatch.setenv("MANPAGER", "sh -c 'col -bx'")
    monkeypatch.setenv("PAGER", "less")
    monkeypatch.setenv("LESS", "FX")
    monkeypatch.setattr(_output.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_output.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    _output.LessPager().show("\x1b[94mci\x1b[0m")
    assert seen["cmd"] == ["less"]
    assert seen["env"]["LESS"] == "FX -R"
    assert seen["input"] == "\x1b[94mci\x1b[0m"
