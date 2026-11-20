"""CLI surface: the exit-0 invariant, log levels, replay, and `config`."""

import json
import logging
import sys
from pathlib import Path

import pytest
from rich.color import ColorSystem

from ci_doctor import cli
from ci_doctor.cli._console import terminal
from ci_doctor.cli._console.output import pager
from ci_doctor.cli.analyze import AnalyzeCommand
from ci_doctor.errors import SetupError
from tests import support
from tests.support import invoke


def _run(*argv):
    """Run `ci-doctor argv`, which must exit 0, and return its stdout."""
    result = invoke(*argv)
    assert result.exit_code == 0, result.output
    return result.stdout


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


def test_a_crash_inside_analyze_exits_zero(monkeypatch):
    """Invariant #3: once analysis runs, a crash exits 0, so a pipeline outcome never changes."""

    def crash(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(AnalyzeCommand, "_analyze", crash)
    result = invoke("analyze", "123")
    assert result.exit_code == 0
    assert "internal error, exiting 0" in result.stderr


def test_a_crash_anywhere_else_exits_one(monkeypatch):
    """Only analyze holds invariant #3; any other command's crash is reported like any CLI's."""

    def crash(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("ci_doctor.cli.config.load_config", crash)
    result = invoke("config")
    assert result.exit_code == 1
    assert "internal error: RuntimeError: boom" in result.stderr


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
def test_a_broken_setup_exits_one(argv, monkeypatch, tmp_path):
    """Invariant #3: every way the setup can be wrong is seen, before analysis starts."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ci-doctor", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()  # the entry point: click's usage error is 2, ci-doctor's is 1
    assert exc.value.code == 1


def test_an_empty_include_says_how_to_pick_matchers(monkeypatch, tmp_path):
    """The zero-config run fails with the next step, not a stack of nothing."""
    monkeypatch.chdir(tmp_path)
    result = invoke("analyze", "123")
    assert "ci-doctor matchers add official/<name>" in " ".join(result.stderr.split())  # rich wraps


def test_a_setup_error_is_the_one_exit_one(monkeypatch):
    """Invariant #3: a broken setup exits 1 and says why; nothing else does."""

    def broken_setup(*_args, **_kwargs):
        raise SetupError("registry 'acme': index.yaml is out of date")

    monkeypatch.setattr("ci_doctor.cli.config.load_config", broken_setup)
    result = invoke("config")
    assert result.exit_code == 1
    assert "index.yaml is out of date" in result.stderr


@pytest.mark.parametrize(
    ("flag", "env", "expected"),
    [
        (None, None, logging.INFO),
        ("warning", None, logging.WARNING),
        (None, "debug", logging.DEBUG),
        ("error", "debug", logging.ERROR),
        (None, "nonsense", logging.INFO),
    ],
)
def test_the_log_level_comes_from_the_flag_then_the_env_then_info(monkeypatch, flag, env, expected):
    """`--log-level` wins over `CI_DOCTOR_LOG_LEVEL`; an unknown env value falls back to info."""
    if env is None:
        monkeypatch.delenv("CI_DOCTOR_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("CI_DOCTOR_LOG_LEVEL", env)
    terminal.configure_logging(flag)
    assert logging.getLogger("ci_doctor").level == expected


@pytest.mark.parametrize("provider", support.PROVIDERS)
def test_from_file_replay_smoke(provider, registry_config):
    """Offline replay renders a report and writes both artifacts."""
    tmp_path = registry_config  # report.md/report.json land here, not in the repo
    log = _job_log(tmp_path, provider)
    result = invoke("--no-color", "analyze", log)
    assert result.exit_code == 0

    out = result.stdout
    assert "Root cause" in out
    assert "script" in out
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()


STAGES = (
    "config: ci ",
    "matchers: 2 loaded from fx",
    "analyzing 1 job(s)",
    "blamed the",
    "evidence kept",
    "deterministic report",
)


@pytest.mark.parametrize(("level", "shown"), [((), True), (("--log-level", "warning"), False)])
def test_info_shows_each_stage_of_a_run_and_never_the_log(monkeypatch, registry_config, level, shown):
    """One line per stage at the default level, none at `warning`; never a line of the log itself."""
    monkeypatch.delenv("CI_DOCTOR_LOG_LEVEL", raising=False)
    log = _job_log(registry_config, support.PROVIDERS[0])
    result = invoke("--no-color", *level, "analyze", log)
    err = result.stderr
    assert [stage in err for stage in STAGES] == [shown] * len(STAGES)
    content = [line.strip() for line in Path(log).read_text().splitlines() if len(line.strip()) > 12]
    assert content and not [line for line in content if line in err]


@pytest.mark.parametrize("provider", support.PROVIDERS)
def test_format_json_puts_a_parseable_report_on_stdout(provider, registry_config):
    """`--format json` is what an agent reads: stdout must parse, with no panels in it."""
    tmp_path = registry_config
    log = _job_log(tmp_path, provider)
    result = invoke("analyze", "--format", "json", log)
    assert result.exit_code == 0

    reports = json.loads(result.stdout)  # the whole of stdout, not a fragment of it
    assert reports and {"summary", "failure_phase", "handoff_prompt"} <= reports[0].keys()
    assert "Root cause" not in result.stdout  # the rendered panels are suppressed
    # The artifacts still land, and the human-facing chatter stays on stderr so it
    # can never end up inside what the caller is parsing.
    assert (tmp_path / "report.json").exists()
    assert "wrote report.md" in result.stderr


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


def test_config_schema_is_valid_json(monkeypatch, tmp_path):
    """`--schema` emits parseable JSON Schema carrying the published $id."""
    monkeypatch.chdir(tmp_path)  # no stray .ci-doctor.yml from the repo root
    schema = json.loads(_run("config", "--schema"))
    assert schema["$id"].endswith("ci-doctor.schema.json")
    assert "extraction" in schema["properties"]


def test_config_prints_the_effective_config(monkeypatch, tmp_path):
    """The bare command shows the user's layer merged onto the defaults."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci-doctor.yml").write_text("llm:\n  model: my-model\n")
    out = _run("--no-color", "config")
    assert "my-model" in out  # the user layer
    assert "tail_lines" in out  # and the untouched defaults


def test_config_diff_shows_only_what_the_user_changed(monkeypatch, tmp_path):
    """`--diff` shows the replaced default and the new value, nothing untouched."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci-doctor.yml").write_text("llm:\n  model: my-model\n")
    out = _run("--no-color", "config", "--diff")
    assert "-  model: null" in out
    assert "+  model: my-model" in out
    assert "tail_lines" not in out  # unchanged keys stay out of the diff


def test_config_diff_is_empty_on_stock_defaults(monkeypatch, tmp_path):
    """With no user config, `--diff` says so instead of printing an empty diff."""
    monkeypatch.chdir(tmp_path)
    assert "no differences" in _run("--no-color", "config", "--diff")


def test_repeated_config_flags_apply_left_to_right(monkeypatch, tmp_path):
    """Several -f files stack, and the rightmost wins on a key they both set."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.yml").write_text("llm:\n  model: from-a\n  temperature: 0.9\n")
    (tmp_path / "b.yml").write_text("llm:\n  model: from-b\n")
    out = _run("--no-color", "-f", "a.yml", "-f", "b.yml", "config")
    assert "model: from-b" in out  # the rightmost file wins
    assert "temperature: 0.9" in out  # keys only the earlier file set survive


def test_config_validate_reports_ok_and_failure(registry_config):
    """`--validate` is the dry run: a bad key exits 1 naming it; a good setup counts its matchers."""
    (registry_config / "bad.yml").write_text("llm:\n  nope: 1\n")
    result = invoke("-f", "bad.yml", "config", "--validate")
    assert result.exit_code == 1
    assert "invalid config" in result.stderr  # named, not raised

    out = _run("config", "--validate")
    assert "config ok" in out
    assert "2 matchers from 1 registries" in out


def test_analyze_reports_a_bad_config_as_a_config_error(monkeypatch, tmp_path):
    """A rejected regex is the user's config, not an internal error."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "bad.yml").write_text("denoise:\n  noise_patterns: ['(?=x)']\n")
    (tmp_path / "job.log").write_text("boom\n")
    result = invoke("-f", "bad.yml", "analyze", "job.log")
    err = result.stderr
    assert result.exit_code == 1
    assert "invalid config" in err
    assert "internal error" not in err


def test_the_config_pager_ignores_manpager_and_keeps_colours(monkeypatch):
    """Pydoc prefers $MANPAGER; a `col -bx` one ate the escapes. Ours runs $PAGER with less -R."""
    seen = {}
    monkeypatch.setenv("MANPAGER", "sh -c 'col -bx'")
    monkeypatch.setenv("PAGER", "less")
    monkeypatch.setenv("LESS", "FX")
    monkeypatch.setattr(pager.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pager.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    pager.LessPager().show("\x1b[94mci\x1b[0m")
    assert seen["cmd"] == ["less"]
    assert seen["env"]["LESS"] == "FX -R"
    assert seen["input"] == "\x1b[94mci\x1b[0m"


def test_a_setup_error_is_logged_at_error_level_without_colour_under_plain(monkeypatch, tmp_path):
    """The failure reads like the other log lines, `ERROR` first, and `--plain` leaves it uncoloured."""
    monkeypatch.chdir(tmp_path)
    # A colour terminal, were it not for --plain: rich picks its colour system at construction.
    monkeypatch.setattr(terminal.LOG_CONSOLE, "_force_terminal", True)
    monkeypatch.setattr(terminal.LOG_CONSOLE, "_color_system", ColorSystem.STANDARD)
    result = invoke("--plain", "analyze", "123")
    err = result.stderr
    assert result.exit_code == 1
    assert err.lstrip().startswith("INFO     config:")
    assert "ERROR    no matchers or bundles selected" in err
    assert "\x1b[" not in err


def test_every_command_has_examples_that_name_it():
    """Each `--help` shows examples, and each example runs the command it documents.

    A rename that forgets its examples fails here instead of in a user's terminal.
    """
    from ci_doctor.cli import COMMANDS as ROOT
    from ci_doctor.cli.agent import COMMANDS as AGENT
    from ci_doctor.cli.cache import COMMANDS as CACHE
    from ci_doctor.cli.matchers import COMMANDS as MATCHERS
    from ci_doctor.cli.registry import COMMANDS as REGISTRY

    groups = {"": ROOT, "matchers ": MATCHERS, "registry ": REGISTRY, "cache ": CACHE, "agent ": AGENT}
    wrong = {}
    for group, commands in groups.items():
        for command in commands:
            prefix = f"ci-doctor {group}{command.name}"
            bad = [e for e in command.examples if not e.startswith(prefix)]
            if not command.examples or bad:
                wrong[f"{group}{command.name}"] = bad or "no examples"
    assert wrong == {}
