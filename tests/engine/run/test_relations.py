"""The run layer: relations and windows, at the edges of the log."""

from ci_doctor.engine import compile_plan, run
from tests.engine.support import v1


def _anchors(lines, **fields):
    return sorted(w.anchor for w in run(compile_plan([v1(**fields)]), lines))


def _spans(lines, **fields):
    return sorted((w.start, w.end) for w in run(compile_plan([v1(**fields)]), lines))


def test_all_any_not_are_per_line():
    """Set operations on one line's hits."""
    lines = ["error: a", "error: b skip", "warn: c", "fatal: d"]
    match = {"all": [{"any": [{"regex": "^error"}, {"regex": "^fatal"}]}, {"not": {"regex": "skip"}}]}
    assert _anchors(lines, match=match) == [0, 3]


def test_preceded_by_looks_only_within_n_lines_above():
    """The anchor's own line doesn't count, and nothing above line 0 exists."""
    lines = ["HEAD", "x", "x", "FOOTER", "x", "FOOTER", "HEAD FOOTER"]
    match = {"all": [{"regex": "FOOTER"}, {"preceded_by": {"regex": "HEAD", "within": 3}}]}
    assert _anchors(lines, match=match) == [3]


def test_followed_by_looks_only_within_n_lines_below():
    """Ruff's full format: the rule line, then `-->` within two lines; the last line has nothing below."""
    lines = ["E501 long", "  -->  a.py:1:1", "F401 unused", "x", "x", "  --> b.py:2:2", "E999 last"]
    match = {"all": [{"regex": "^[A-Z]+[0-9]+ "}, {"followed_by": {"regex": "-->", "within": 2}}]}
    assert _anchors(lines, match=match) == [0]


def test_inside_a_block_both_ends_included_and_unclosed_blocks_capped():
    """A block closes at its end, or after max_lines; a start inside an open block opens nothing."""
    lines = [
        "[test] start",
        "boom 1",
        "[test] start",
        "[test] end",
        "boom 2",
        "[test] start",
        "boom 3",
        "boom 4",
    ]
    match = {
        "all": [
            {"regex": "boom|start|end"},
            {"inside": {"start": {"regex": "start"}, "end": {"regex": "end"}, "max_lines": 2}},
        ]
    }
    assert _anchors(lines, match=match) == [0, 1, 2, 3, 5, 6]


def test_not_inside_excludes_a_block():
    """`not: inside:` is how a test failure keeps clear of a compile block."""
    lines = ["[compile]", "error x", "[done]", "error y"]
    match = {
        "all": [
            {"regex": "^error"},
            {
                "not": {
                    "inside": {
                        "start": {"regex": r"^\[compile"},
                        "end": {"regex": r"^\[done"},
                        "max_lines": 50,
                    }
                }
            },
        ]
    }
    assert _anchors(lines, match=match) == [3]


def test_window_before_after_clamp_to_the_log():
    """A window never reaches before line 0 or past the last line."""
    lines = ["hit", "x", "hit"]
    assert _spans(lines, match={"regex": "hit"}, window={"before": 5, "after": 5}) == [(0, 3), (0, 3)]


def test_window_from_starts_at_the_nearest_header_within_reach():
    """Go build: the `# package` header above the error; out of reach, the anchor itself."""
    lines = ["# pkg/a", "a.go:1: err", "x", "x", "x", "a.go:9: err"]
    window = {"from": {"regex": "^# ", "within": 3}}
    assert _spans(lines, match={"regex": r"\.go:\d+: err"}, window=window) == [(0, 2), (5, 6)]


def test_window_until_runs_to_the_end_line_or_max_lines_and_never_nests():
    """Pytest's block: an anchor inside an open window starts nothing new."""
    lines = ["=== FAILURES", "a", "=== FAILURES", "=== short summary", "=== FAILURES", "b", "c", "d"]
    window = {"until": {"regex": "short summary"}, "max_lines": 4}
    assert _spans(lines, match={"regex": "FAILURES"}, window=window) == [(0, 4), (4, 8)]
    capped = {"until": {"regex": "short summary"}, "max_lines": 2}
    assert _spans(lines[:4], match={"regex": "FAILURES"}, window=capped) == [(0, 2), (2, 4)]


def test_window_while_extends_over_the_chain_up_to_max_lines():
    """A JVM exception and its `at` / `Caused by:` lines, which have no closing line."""
    lines = [
        "Exception in thread main",
        "  at a",
        "  at b",
        "Caused by: x",
        "  at c",
        "next",
        "Exception in thread t",
        "  at z",
    ]
    window = {"while": {"regex": r"^\s+at |^Caused by: "}, "max_lines": 4}
    assert _spans(lines, match={"regex": "^Exception in thread "}, window=window) == [(0, 4), (6, 8)]


def test_variants_report_which_spelling_anchored():
    """`examine` and `verify` say `ruff.check (full)`, so a window knows its variant."""
    m = v1(variants={"old": {"match": {"regex": "^npm ERR!"}}, "new": {"match": {"regex": "^npm error"}}})
    wins = run(compile_plan([m]), ["npm error code E404", "x", "npm ERR! code E404"])
    assert sorted((w.anchor, w.variant) for w in wins) == [(0, "new"), (2, "old")]
