"""`window.condense`: over budget, a window's other lines go before any window does."""

from ci_doctor.engine import estimate_tokens
from tests.engine.support import extract, rx, v1

FAILURES = [
    "=== FAILURES ===",
    *[
        line
        for i in range(10)
        for line in (
            f"_____ test_{i} _____",
            *[f"    source line {i}.{k} echoed from the test" for k in range(30)],
            f"E   assert {i} == 0",
        )
    ],
]
SUMMARY = [
    "=== short test summary info ===",
    *[f"FAILED t.py::test_{i}" for i in range(10)],
    "=== 10 failed in 1s ===",
]
LINES = FAILURES + SUMMARY

FAILURES_MATCHER = v1(
    id="failures",
    match={"regex": "^=+ FAILURES"},
    window={
        "until": {"regex": "^=+ short test summary"},
        "max_lines": 500,
        "condense": {"regex": "^(E |_+ )"},
    },
)
SUMMARY_MATCHER = v1(
    id="summary",
    match={"regex": "^=+ short test summary"},
    window={"until": {"regex": r"failed in"}, "max_lines": 500},
)


def test_over_budget_the_condensed_lines_stay_and_each_cut_is_marked():
    """The anchor, every `E` line and the whole summary fit; the echoed source goes, announced."""
    out = extract(LINES, [FAILURES_MATCHER, SUMMARY_MATCHER], tail_lines=0, max_tokens=400)
    assert out[0] == "=== FAILURES ==="
    assert [line for line in out if line.startswith("E ")] == [f"E   assert {i} == 0" for i in range(10)]
    assert all(line in out for line in SUMMARY)
    assert not [line for line in out if "source line" in line]
    assert sum("lines elided" in line for line in out) == 10  # one per dropped run
    assert estimate_tokens("\n".join(out)) <= 400


def test_under_budget_the_window_goes_in_whole():
    """`condense` is a budget step, not a filter: a line it doesn't match may be the one that matters."""
    out = extract(LINES, [FAILURES_MATCHER, SUMMARY_MATCHER], tail_lines=0, max_tokens=100_000)
    assert out == LINES


def test_without_a_budget_nothing_is_condensed():
    """`verify` and `examine` select with no budget, so their goldens never change."""
    assert extract(LINES, [FAILURES_MATCHER, SUMMARY_MATCHER], tail_lines=0) == LINES


def test_a_line_another_window_keeps_whole_is_never_condensed_away():
    """A line stays droppable only if every window covering it says so."""
    other = rx("source line 4.15 ", id="other", after=1)
    out = extract(LINES, [FAILURES_MATCHER, SUMMARY_MATCHER, other], tail_lines=0, max_tokens=400)
    assert "    source line 4.15 echoed from the test" in out
    assert "    source line 4.16 echoed from the test" in out
    assert "    source line 4.17 echoed from the test" not in out


def test_the_tail_never_keeps_a_line_a_matcher_condensed_away():
    """The tail is a guess about position, shed before anything; it can't overrule a matcher."""
    out = extract(LINES, [FAILURES_MATCHER, SUMMARY_MATCHER], tail_lines=100, max_tokens=400)
    assert not [line for line in out if "source line" in line]
    assert all(line in out for line in SUMMARY)


def test_a_tail_alone_is_never_condensed():
    """With no matcher window to condense, the tail goes in as it is and `fit` cuts it."""
    lines = [f"plain line {i}" for i in range(50)]
    assert extract(lines, [], tail_lines=20, max_tokens=10)[1:] == lines[-20:]
