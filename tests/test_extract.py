"""Windowed extraction: anchors, merging, and visible elision."""

from ci_doctor.config.schema import MatcherConfig
from ci_doctor.core.budget import estimate_tokens
from ci_doctor.core.extract import extract


def _m(**kw):
    """Build a MatcherConfig with every field defaulted, overridden by kwargs."""
    return MatcherConfig(**{"id": "x", **kw})


def test_tail_window_only():
    """With no matchers, the tail window carries the evidence."""
    lines = [f"line{i}" for i in range(200)]
    out = extract(lines, [], tail_lines=10)
    assert out[0].startswith("… [190 lines elided]")
    assert out[-1] == "line199"
    assert "line195" in out


def test_anchored_window_context():
    """A pattern matcher keeps its `before`/`after` context lines."""
    lines = ["a", "b", "c", "BOOM error here", "d", "e", "f"]
    out = extract(lines, [_m(pattern="BOOM", before=1, after=1)], tail_lines=0)
    assert "c\nBOOM error here\nd" in "\n".join(out)
    assert "elided" in out[0]  # head before the window is elided visibly


def test_overlapping_windows_merge_to_contiguous():
    """Adjacent windows merge, so no elision marker splits contiguous output."""
    lines = ["l0", "hit1", "l2", "l3", "hit2", "l5"]
    out = extract(lines, [_m(pattern="hit", before=1, after=1)], tail_lines=0)
    assert out == lines  # two adjacent windows merged; nothing elided


def test_start_end_block():
    """A start/end matcher captures the whole block and elides both sides."""
    lines = ["pre", "=== FAILURES ===", "detail1", "detail2", "=== short test summary ===", "after"]
    out = extract(lines, [_m(id="pytest", start="=+ FAILURES", end="short test summary")], tail_lines=0)
    assert "=== FAILURES ===\ndetail1\ndetail2\n=== short test summary ===" in "\n".join(out)
    assert out[0].startswith("… [")  # "pre" elided
    assert out[-1].startswith("… [")  # "after" elided


# Why roles exist. Without budget-aware selection the cut is positional
# (`budget.fit` keeps the tail), which is exactly backwards here: npm reports the
# child process failed *after* the compiler said what was wrong.
def test_budget_pressure_sheds_the_wrapper_not_the_tool():
    """Under budget the compiler errors survive and the npm epilogue is shed."""
    lines = [f"src/a{i}.ts:1:1 - error TS2345: bad argument" for i in range(40)]
    # Unmatched output between the blocks: windows only stay separable — and so
    # only become rankable — across a gap, since `_merge` fuses adjacent ones.
    lines += [f"  building bundle chunk {i}" for i in range(20)]
    lines += [f"npm ERR! code ELIFECYCLE {i}" for i in range(400)]
    matchers = [_m(id="tsc", pattern="error TS"), _m(id="npm", pattern="npm ERR!", role="wrapper")]

    budget = estimate_tokens("\n".join(lines)) // 2
    out = "\n".join(extract(lines, matchers, tail_lines=0, max_tokens=budget))

    assert "error TS2345" in out, "dropped the cause and kept the complaint"
    assert "npm ERR!" not in out, "kept the wrapper epilogue that blew the budget"


def test_one_oversized_window_is_never_dropped_to_nothing():
    """A single window over budget stays: cutting *inside* it is `budget.fit`'s job."""
    lines = [f"=== FAILURES ===   detail line {i}" for i in range(500)]
    out = extract(lines, [_m(pattern="FAILURES")], tail_lines=0, max_tokens=10)
    assert any("detail line" in line for line in out)


def _budgeted(lines, matchers):
    """Extract at half the cost of the whole log, so exactly one of two windows fits."""
    return "\n".join(
        extract(lines, matchers, tail_lines=0, max_tokens=estimate_tokens("\n".join(lines)) // 2)
    )


def test_a_later_wrapper_window_never_outranks_an_earlier_tool_window():
    """Role beats position: the wrapper printed last, and still loses."""
    lines = [f"error TS2345: bad argument {i}" for i in range(100)]
    lines += [f"  building chunk {i}" for i in range(20)]
    lines += [f"npm ERR! code ELIFECYCLE {i}" for i in range(100)]
    out = _budgeted(
        lines, [_m(id="tsc", pattern="error TS"), _m(id="npm", pattern="npm ERR!", role="wrapper")]
    )
    assert "error TS2345" in out
    assert "npm ERR!" not in out


def test_within_a_role_the_later_window_survives():
    """A job stops at its first failing command, so the earlier same-role output was survived."""
    lines = [f"lint error {i}" for i in range(100)]
    lines += [f"  compiling {i}" for i in range(20)]
    lines += [f"build error {i}" for i in range(100)]
    out = _budgeted(lines, [_m(id="lint", pattern="lint error"), _m(id="build", pattern="build error")])
    assert "build error" in out
    assert "lint error" not in out


def test_fallback_is_shed_before_a_wrapper():
    """The no-pack guess is the least informative window of all."""
    lines = [f"make: *** [all] Error {i}" for i in range(100)]
    lines += [f"  step {i}" for i in range(20)]
    lines += [f"ERROR something {i}" for i in range(100)]
    matchers = [
        _m(id="make", pattern=r"make: \*\*\*", role="wrapper"),
        _m(id="generic", pattern="^ERROR", role="fallback"),
    ]
    out = _budgeted(lines, matchers)
    assert "make: ***" in out
    assert "ERROR something" not in out


def test_a_merged_window_keeps_the_higher_role():
    """A tool window touching a fallback window is not demoted by the merge."""
    lines = ["ERROR first", "error TS1: cause"]
    lines += [f"  pad {i}" for i in range(20)]
    lines += [f"npm ERR! {i}" for i in range(30)]
    matchers = [
        _m(pattern="^ERROR", role="fallback"),
        _m(pattern="error TS"),
        _m(pattern="npm ERR!", role="wrapper"),
    ]
    out = _budgeted(lines, matchers)
    assert "ERROR first" in out and "error TS1: cause" in out
    assert "npm ERR!" not in out
