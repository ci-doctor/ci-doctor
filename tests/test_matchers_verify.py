"""`ci-doctor matchers verify`: golden fixtures, the 3 + 2 minimum, and the exemption list."""

import shutil
import sys
from pathlib import Path

import pytest

from ci_doctor import cli
from ci_doctor.engine import MatcherV1, compile_plan, run
from ci_doctor.errors import SetupError
from ci_doctor.matchers.index import write_index
from ci_doctor.matchers.logs import read_fixture
from ci_doctor.matchers.validate import load_matcher
from ci_doctor.matchers.verify import Policy, selection, verify
from tests import support
from tests.support import gh_log, gitlab_log

PYTEST_DIR = "matchers/python/pytest"
FIX = f"{PYTEST_DIR}/fixtures"

PYTEST_OUT = [
    "F.                                                                       [100%]",
    "=================================== FAILURES ===================================",
    "__________________________________ test_total __________________________________",
    "",
    "    def test_total():",
    ">       assert total([1000, 1700]) == 3000",
    "E       assert 2700 == 3000",
    "",
    "tests/test_cart.py:7: AssertionError",
    "=========================== short test summary info ============================",
    "FAILED tests/test_cart.py::test_total - assert 2700 == 3000",
    "1 failed, 1 passed in 0.03s",
]
TRACEBACK = [
    "Traceback (most recent call last):",
    '  File "/app/cart/report.py", line 9, in main',
    "    print(total([]) / len([]))",
    "ZeroDivisionError: division by zero",
]
WARNINGS = [
    "$ pytest -q",
    "..                                                                       [100%]",
    "=============================== warnings summary ===============================",
    "  /app/cart.py:3: DeprecationWarning: float totals are deprecated",
    "2 passed, 1 warning in 0.02s",
]


def _plain(lines: list[str]) -> str:
    """Tool output with no runner framing, as a registry fixture often is."""
    return "\n".join(lines) + "\n"


#: 3 positive and 2 negative per matcher: the minimum `verify` asks for.
FIXTURES = {
    "pytest": {
        "assert_plain": _plain(["$ pytest -q", *PYTEST_OUT]),
        "assert.github": gh_log([("Run pytest -q", PYTEST_OUT)]),
        "assert.gitlab": gitlab_log([("step_script", ["$ pytest -q", *PYTEST_OUT])]),
        "uncaught_exception": _plain(["$ python -m cart.report", *TRACEBACK]),
        "passing_with_warnings": _plain(WARNINGS),
    },
    "python_traceback": {
        "zero_division": _plain(["$ python -m cart.report", *TRACEBACK]),
        "traceback.github": gh_log([("Run python -m cart.report", TRACEBACK)]),
        "traceback.gitlab": gitlab_log([("step_script", ["$ python -m cart.report", *TRACEBACK])]),
        "pytest_assert": _plain(["$ pytest -q", *PYTEST_OUT]),
        "warnings_only": _plain(WARNINGS),
    },
}


@pytest.fixture
def registry(tmp_path):
    """The two-matcher registry with its fixture logs, `.expected` files blessed."""
    root = support.write_registry(tmp_path / "registry", support.PY_MATCHERS, support.PY_BUNDLES, FIXTURES)
    verify(root, update=True)
    return root


def _drop(root: Path, *stems: str) -> None:
    """Delete fixtures (log and expected) from the pytest matcher."""
    for stem in stems:
        (root / FIX / f"{stem}.log").unlink()
        (root / FIX / f"{stem}.expected").unlink()


def test_a_blessed_registry_verifies(registry):
    """Two matchers, each with 3 positive and 2 negative fixtures, all matching."""
    assert verify(registry) == 2


def test_a_changed_positive_output_fails_with_a_diff(registry):
    """The `.expected` file is the contract; any drift is shown line by line."""
    expected = registry / FIX / "assert_plain.expected"
    expected.write_text(expected.read_text().replace("assert 2700 == 3000", "assert 2700 == 2900"))
    with pytest.raises(SetupError, match="assert_plain.log: pytest differs") as err:
        verify(registry)
    assert "-E       assert 2700 == 2900" in str(err.value)
    assert "+E       assert 2700 == 3000" in str(err.value)


def test_a_negative_fixture_fails_when_the_matcher_fires(registry):
    """An empty `.expected` means silence; a fire there is the bug a negative exists to catch."""
    shutil.copy(registry / FIX / "assert_plain.log", registry / FIX / "passing_with_warnings.log")
    with pytest.raises(SetupError, match="passing_with_warnings.log: pytest differs"):
        verify(registry)


def test_no_window_is_empty_output_not_the_whole_log(registry):
    """`select()` falls back to every line when nothing matched; verify must not."""
    m = load_matcher(registry / PYTEST_DIR / "matcher.yaml")
    lines = read_fixture(registry / FIX / "uncaught_exception.log")
    assert selection(lines, run(compile_plan([m]), lines)) == ""


def test_a_missing_expected_file_fails(registry):
    """A log with no `.expected` asserts nothing, so it is an error, not a skip."""
    (registry / FIX / "assert_plain.expected").unlink()
    with pytest.raises(SetupError, match="no assert_plain.expected"):
        verify(registry)


@pytest.mark.parametrize(
    ("dropped", "counts"),
    [(["assert_plain"], "2 positive and 2 negative"), (["uncaught_exception"], "3 positive and 1 negative")],
)
def test_the_fixture_minimum_is_three_positive_and_two_negative(registry, dropped, counts):
    """A matcher with too few fixtures has not shown it fires right, or stays quiet."""
    _drop(registry, *dropped)
    with pytest.raises(SetupError, match=f"pytest: has {counts} fixtures"):
        verify(registry)


def test_an_exempt_matcher_needs_only_one_positive(registry):
    """`verify-exempt.txt` lets the 38 migrated matchers ship before they are backfilled."""
    _drop(registry, "assert.github", "assert.gitlab", "uncaught_exception", "passing_with_warnings")
    (registry / Policy().exempt).write_text("# migrated, backfill pending\npytest\n")
    assert verify(registry) == 2


def test_an_exempt_matcher_still_needs_one_positive(registry):
    """The exemption lowers the bar to one positive, not to zero."""
    _drop(registry, "assert_plain", "assert.github", "assert.gitlab")
    (registry / Policy().exempt).write_text("pytest\n")
    with pytest.raises(SetupError, match="needs at least 1 positive"):
        verify(registry)


def test_an_exemption_for_an_unknown_name_fails(registry):
    """A stale line in the list would otherwise exempt whatever later takes the name."""
    (registry / Policy().exempt).write_text("pytest\nno_such_matcher\n")
    with pytest.raises(SetupError, match="'no_such_matcher' is not a matcher"):
        verify(registry)


def test_an_exempt_matcher_that_meets_the_bar_must_leave_the_list(registry):
    """The list only shrinks: once backfilled, staying exempt is an error."""
    (registry / Policy().exempt).write_text("pytest\n")
    with pytest.raises(SetupError, match="remove it from verify-exempt.txt"):
        verify(registry)


def test_update_writes_the_expected_files(registry):
    """`--update` writes what the matcher selects: its window, gaps marked, blanks gone."""
    for expected in (registry / FIX).glob("*.expected"):
        expected.unlink()
    verify(registry, update=True)
    assert (registry / FIX / "assert_plain.expected").read_text() == (
        "… [2 lines elided] …\n"
        "=================================== FAILURES ===================================\n"
        "__________________________________ test_total __________________________________\n"
        "    def test_total():\n"
        ">       assert total([1000, 1700]) == 3000\n"
        "E       assert 2700 == 3000\n"
        "tests/test_cart.py:7: AssertionError\n"
        "=========================== short test summary info ============================\n"
        "… [2 lines elided] …\n"
    )
    assert (registry / FIX / "passing_with_warnings.expected").read_text() == ""
    assert verify(registry) == 2


def test_a_fixture_over_the_size_cap_fails(registry):
    """Fixtures are trimmed logs, not whole job dumps."""
    log = registry / FIX / "assert_plain.log"
    log.write_text(log.read_text() + "padding line\n" * 20000)
    with pytest.raises(SetupError, match="over 204800"):
        verify(registry)


def test_github_gitlab_and_plain_logs_select_the_same_lines(registry):
    """Runner framing is stripped before matching, so one tool output is one result.

    Elision counts differ with each runner's own lines, so compare the selected text.
    """
    m = load_matcher(registry / PYTEST_DIR / "matcher.yaml")

    def selected(stem: str) -> list[str]:
        lines = read_fixture(registry / FIX / f"{stem}.log")
        out = selection(lines, run(compile_plan([m]), lines)).splitlines()
        return [line.strip() for line in out if not line.startswith("… [")]

    assert selected("assert.github") == selected("assert.gitlab") == selected("assert_plain")


def test_the_cli_exits_one_when_verify_fails(registry, monkeypatch):
    """A broken matcher turns the registry's CI step red."""
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "matchers", "verify", str(registry)])
    (registry / FIX / "assert_plain.expected").write_text("nothing like the output\n")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_a_framed_fixture_reads_only_the_blamed_step(tmp_path):
    """As `analyze` does: context from an earlier step never reaches a relation or a window."""
    log = tmp_path / "x.github.log"
    log.write_text(gh_log([("Run npm ci", ["added 842 packages"]), ("Run npm test", ["boom: test failed"])]))
    lines = read_fixture(log)
    assert "boom: test failed" in lines
    assert "added 842 packages" not in lines
    relation = MatcherV1.model_validate(
        {
            "schema": 1,
            "id": "x",
            "description": "A matcher whose context sits in an earlier step.",
            "tags": ["t"],
            "match": {"all": [{"regex": "^boom"}, {"preceded_by": {"regex": "added", "within": 50}}]},
        }
    )
    assert selection(lines, run(compile_plan([relation]), lines)) == ""


@pytest.mark.parametrize("command", [verify, write_index])
def test_a_directory_with_no_matchers_is_not_a_registry(tmp_path, command):
    """A typo'd path must not pass as "0 matchers verified"."""
    with pytest.raises(SetupError, match="no matchers"):
        command(tmp_path)


# Schema 1: per-output goldens, variant coverage, runner trailers, cross-fire, policy flags.

DESC = "An output description long enough for the rules."
NPM = {
    "schema": 1,
    "description": "npm, in a test registry: install failures and failed scripts.",
    "tags": ["js"],
    "outputs": {
        "install": {
            "description": DESC,
            "variants": {
                "legacy": {"match": {"regex": "^npm ERR! code E404"}},
                "current": {"match": {"regex": "^npm error code E404"}},
            },
        },
        "run": {
            "description": DESC,
            "role": "wrapper",
            "match": {"regex": "^npm (ERR!|error) Lifecycle script"},
        },
    },
}
NPM_FIXTURES = {
    "e404_old.github": gh_log([("Run npm ci", ["npm ERR! code E404", "npm ERR! 404 Not Found"])]),
    "e404_new.gitlab": gitlab_log([("step_script", ["$ npm ci", "npm error code E404"])]),
    "script": _plain(["> test", "npm error Lifecycle script `test` failed"]),
}


@pytest.fixture
def npm(tmp_path):
    """A two-output schema 1 matcher, its `.expected` files blessed."""
    root = support.write_registry(tmp_path / "reg", {"npm": NPM}, fixtures={"npm": NPM_FIXTURES})
    verify(root, update=True, policy=Policy(min_positive=1, min_negative=1))
    return root


def test_each_output_has_its_own_golden_per_fixture(npm):
    """`<case>.<output>.expected`: the install output fires on two fixtures, the run output on one."""
    fixtures = npm / "matchers/js/npm/fixtures"
    assert "npm ERR! code E404\n" in (fixtures / "e404_old.github.install.expected").read_text()
    assert (fixtures / "e404_old.github.run.expected").read_text() == ""
    assert (fixtures / "script.run.expected").read_text().strip().endswith("failed")
    assert verify(npm, policy=Policy(min_positive=1, min_negative=1)) == 1


def test_framing_comes_from_the_name_not_the_content(npm):
    """A GitHub log named plain keeps its timestamps, so a `^` anchor no longer matches."""
    fixtures = npm / "matchers/js/npm/fixtures"
    (fixtures / "e404_old.github.log").rename(fixtures / "e404_old.log")
    for out in ("install", "run"):
        (fixtures / f"e404_old.github.{out}.expected").rename(fixtures / f"e404_old.{out}.expected")
    with pytest.raises(SetupError, match="npm.install differs from e404_old.install.expected"):
        verify(npm, policy=Policy(min_positive=1, min_negative=1))


def test_a_variant_no_fixture_proves_fails(npm):
    """A dead spelling is the bug that hid maven_gradle's Maven half: every variant needs a fixture."""
    fixtures = npm / "matchers/js/npm/fixtures"
    for path in fixtures.glob("e404_new.*"):
        path.unlink()
    with pytest.raises(SetupError, match=r"npm.install \(current\): this variant anchors in none"):
        verify(npm, policy=Policy(min_positive=1, min_negative=1))


def test_the_bar_is_per_output_and_set_by_flags(npm):
    """The official bar (3 + 2) fails here; a registry choosing 1 + 1 passes."""
    with pytest.raises(
        SetupError, match="npm.run: has 1 positive and 2 negative fixtures, needs at least 3 and 2"
    ):
        verify(npm)


def test_an_output_firing_on_the_runner_trailer_fails(tmp_path):
    """It would fire on every failed job, whatever tool ran (GUIDELINES §7)."""
    bad = {"schema": 1, "description": DESC, "tags": ["any"], "match": {"regex": "exit code 1"}}
    root = support.write_registry(
        tmp_path / "reg", {"exits": bad}, fixtures={"exits": {"one": _plain(["exit code 1"])}}
    )
    with pytest.raises(SetupError, match="exits: fires on the runner's own line"):
        verify(root, update=True, policy=Policy(min_positive=1, min_negative=0))


def test_cross_fire_is_reported_and_fails_only_when_asked(registry, tmp_path):
    """python_traceback fires on pytest's fixture: listed in --json, a failure with --cross-fire."""
    log = registry / FIX / "with_traceback.log"
    log.write_text(_plain(["$ pytest", *TRACEBACK, *PYTEST_OUT]))
    verify(registry, update=True, policy=Policy(min_positive=3, min_negative=2))
    out = tmp_path / "report.json"
    verify(registry, json_out=out)
    report = __import__("json").loads(out.read_text())
    assert {"output": "python_traceback", "fixture": f"{FIX}/with_traceback.log"} in report["cross_fire"]
    assert report["outputs"]["pytest"]["positive"] == 4
    with pytest.raises(SetupError, match="python_traceback fires on .*with_traceback.log"):
        verify(registry, policy=Policy(cross_fire=True))
