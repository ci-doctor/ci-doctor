"""`ci-doctor matchers verify`: golden fixtures, the 3 + 2 minimum, and the exemption list."""

import shutil
import sys
from pathlib import Path

import pytest

from ci_doctor import cli
from ci_doctor.errors import SetupError
from ci_doctor.matchers.validate import load_matcher
from ci_doctor.matchers.verify import EXEMPT, matcher_output, read_fixture, verify

FIXTURE = Path(__file__).parent / "fixtures" / "registry"
PYTEST_DIR = "matchers/python/pytest"
FIX = f"{PYTEST_DIR}/fixtures"


@pytest.fixture
def registry(tmp_path):
    """A writable copy of the fixture registry."""
    root = tmp_path / "registry"
    shutil.copytree(FIXTURE, root)
    return root


def _drop(root: Path, *stems: str) -> None:
    """Delete fixtures (log and expected) from the pytest matcher."""
    for stem in stems:
        (root / FIX / f"{stem}.log").unlink()
        (root / FIX / f"{stem}.expected").unlink()


def test_the_fixture_registry_verifies():
    """Two matchers, each with 3 positive and 2 negative fixtures, all matching."""
    assert verify(FIXTURE) == 2


def test_a_changed_positive_output_fails_with_a_diff(registry):
    """The `.expected` file is the contract; any drift is shown line by line."""
    expected = registry / FIX / "assert_plain.expected"
    expected.write_text(expected.read_text().replace("assert 2700 == 3000", "assert 2700 == 2900"))
    with pytest.raises(SetupError, match="assert_plain.log: output differs") as err:
        verify(registry)
    assert "-E       assert 2700 == 2900" in str(err.value)
    assert "+E       assert 2700 == 3000" in str(err.value)


def test_a_negative_fixture_fails_when_the_matcher_fires(registry):
    """An empty `.expected` means silence; a fire there is the bug a negative exists to catch."""
    shutil.copy(registry / FIX / "assert_plain.log", registry / FIX / "passing_with_warnings.log")
    with pytest.raises(SetupError, match="passing_with_warnings.log: output differs"):
        verify(registry)


def test_no_window_is_empty_output_not_the_whole_log():
    """`extract()` falls back to every line when nothing matched; verify must not."""
    m = load_matcher(FIXTURE / PYTEST_DIR / "matcher.yaml")
    assert matcher_output(read_fixture(FIXTURE / FIX / "uncaught_exception.log"), m) == ""


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
    _drop(registry, "assert_github", "assert_gitlab", "uncaught_exception", "passing_with_warnings")
    (registry / EXEMPT).write_text("# migrated, backfill pending\npytest\n")
    assert verify(registry) == 2


def test_an_exempt_matcher_still_needs_one_positive(registry):
    """The exemption lowers the bar to one positive, not to zero."""
    _drop(registry, "assert_plain", "assert_github", "assert_gitlab")
    (registry / EXEMPT).write_text("pytest\n")
    with pytest.raises(SetupError, match="needs at least 1 positive"):
        verify(registry)


def test_an_exemption_for_an_unknown_name_fails(registry):
    """A stale line in the list would otherwise exempt whatever later takes the name."""
    (registry / EXEMPT).write_text("pytest\nno_such_matcher\n")
    with pytest.raises(SetupError, match="'no_such_matcher' is not a matcher"):
        verify(registry)


def test_an_exempt_matcher_that_meets_the_bar_must_leave_the_list(registry):
    """The list only shrinks: once backfilled, staying exempt is an error."""
    (registry / EXEMPT).write_text("pytest\n")
    with pytest.raises(SetupError, match="remove it from verify-exempt.txt"):
        verify(registry)


def test_update_writes_the_expected_files(registry):
    """`--update` regenerates exactly what is committed."""
    for expected in (registry / FIX).glob("*.expected"):
        expected.unlink()
    verify(registry, update=True)
    for expected in (FIXTURE / FIX).glob("*.expected"):
        assert (registry / FIX / expected.name).read_text() == expected.read_text()


def test_a_fixture_over_the_size_cap_fails(registry):
    """Fixtures are trimmed logs, not whole job dumps."""
    log = registry / FIX / "assert_plain.log"
    log.write_text(log.read_text() + "padding line\n" * 20000)
    with pytest.raises(SetupError, match="over 204800"):
        verify(registry)


def test_github_gitlab_and_plain_logs_select_the_same_lines():
    """Runner framing is stripped before matching, so one tool output is one result.

    Elision counts differ with each runner's own lines, and GitHub's timestamp strip
    also takes leading indentation, so compare the selected text.
    """
    m = load_matcher(FIXTURE / PYTEST_DIR / "matcher.yaml")

    def selected(stem: str) -> list[str]:
        out = matcher_output(read_fixture(FIXTURE / FIX / f"{stem}.log"), m).splitlines()
        return [line.strip() for line in out if not line.startswith("… [")]

    assert selected("assert_github") == selected("assert_gitlab") == selected("assert_plain")


def test_the_cli_exits_one_when_verify_fails(registry, monkeypatch):
    """A broken matcher turns the registry's CI step red."""
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "matchers", "verify", str(registry)])
    (registry / FIX / "assert_plain.expected").write_text("nothing like the output\n")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_a_fixture_reads_in_log_order_not_section_order(tmp_path):
    """Lines a segmenter files under the synthetic trailer must not jump ahead of later steps."""
    log = tmp_path / "x.log"
    ts = "2026-07-24T20:04:00.0000000Z "
    log.write_text(
        "".join(
            ts + line + "\n"
            for line in [
                "##[group]Operating System",
                "Ubuntu",
                "##[endgroup]",
                "between groups",
                "##[group]Run pytest",
                "pytest",
                "##[endgroup]",
                "test output",
                "##[error]Process completed with exit code 1.",
                "Post job cleanup.",
            ]
        )
    )
    # The trailer opens at "between groups" and later collects the step's end, so tree
    # order would put those two lines before "pytest".
    assert read_fixture(log) == [
        "Ubuntu",
        "between groups",
        "pytest",
        "test output",
        "##[error]Process completed with exit code 1.",
        "Post job cleanup.",
    ]
