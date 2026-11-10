"""Shedding: under budget pressure the least valuable windows go first."""

from ci_doctor.engine import estimate_tokens
from tests.engine.support import budgeted, extract, matcher


# Why roles exist: `budget.fit` alone keeps the tail, but npm reports the child
# failed *after* the compiler said what was wrong.
def test_budget_pressure_sheds_the_wrapper_not_the_tool():
    """Under budget the compiler errors survive and the npm epilogue is shed."""
    lines = [f"src/a{i}.ts:1:1 - error TS2345: bad argument" for i in range(40)]
    # Unmatched output between the blocks: windows only stay separable — and so
    # only become rankable — across a gap, since `_merge` fuses adjacent ones.
    lines += [f"  building bundle chunk {i}" for i in range(20)]
    lines += [f"npm ERR! code ELIFECYCLE {i}" for i in range(400)]
    matchers = [matcher(id="tsc", pattern="error TS"), matcher(id="npm", pattern="npm ERR!", role="wrapper")]

    budget = estimate_tokens("\n".join(lines)) // 2
    out = "\n".join(extract(lines, matchers, tail_lines=0, max_tokens=budget))

    assert "error TS2345" in out, "dropped the cause and kept the complaint"
    assert "npm ERR!" not in out, "kept the wrapper epilogue that blew the budget"


def test_one_oversized_window_is_never_dropped_to_nothing():
    """A single window over budget stays: cutting *inside* it is `budget.fit`'s job."""
    lines = [f"=== FAILURES ===   detail line {i}" for i in range(500)]
    out = extract(lines, [matcher(pattern="FAILURES")], tail_lines=0, max_tokens=10)
    assert any("detail line" in line for line in out)


def test_a_later_wrapper_window_never_outranks_an_earlier_tool_window():
    """Role beats position: the wrapper printed last, and still loses."""
    lines = [f"error TS2345: bad argument {i}" for i in range(100)]
    lines += [f"  building chunk {i}" for i in range(20)]
    lines += [f"npm ERR! code ELIFECYCLE {i}" for i in range(100)]
    out = budgeted(
        lines, [matcher(id="tsc", pattern="error TS"), matcher(id="npm", pattern="npm ERR!", role="wrapper")]
    )
    assert "error TS2345" in out
    assert "npm ERR!" not in out


def test_within_a_role_the_later_window_survives():
    """A job stops at its first failing command, so the earlier same-role output was survived."""
    lines = [f"lint error {i}" for i in range(100)]
    lines += [f"  compiling {i}" for i in range(20)]
    lines += [f"build error {i}" for i in range(100)]
    out = budgeted(
        lines, [matcher(id="lint", pattern="lint error"), matcher(id="build", pattern="build error")]
    )
    assert "build error" in out
    assert "lint error" not in out


def test_fallback_is_shed_before_a_wrapper():
    """The no-pack guess is the least informative window of all."""
    lines = [f"make: *** [all] Error {i}" for i in range(100)]
    lines += [f"  step {i}" for i in range(20)]
    lines += [f"ERROR something {i}" for i in range(100)]
    matchers = [
        matcher(id="make", pattern=r"make: \*\*\*", role="wrapper"),
        matcher(id="generic", pattern="^ERROR", role="fallback"),
    ]
    out = budgeted(lines, matchers)
    assert "make: ***" in out
    assert "ERROR something" not in out


def test_the_selection_fits_the_budget_it_was_cut_to():
    """Elision markers cost tokens too; uncounted, `fit` overflows and cuts the head."""
    lines = [line for i in range(60) for line in (f"error TS{i}: bad argument", f"  unrelated {i}")]
    budget = estimate_tokens("\n".join(line for line in lines if "error TS" in line))
    out = extract(lines, [matcher(pattern="error TS")], tail_lines=0, max_tokens=budget)
    assert estimate_tokens("\n".join(out)) <= budget


def test_the_tail_is_shed_before_a_fallback():
    """The tail is a guess about position; even the fallback recognised something."""
    lines = [f"ERROR something {i}" for i in range(100)] + [f"  gap {i}" for i in range(20)]
    lines += [f"  trailing {i}" for i in range(100)]
    out = "\n".join(
        extract(
            lines,
            [matcher(pattern="^ERROR", role="fallback")],
            tail_lines=100,
            max_tokens=estimate_tokens("\n".join(lines)) // 2,
        )
    )
    assert "ERROR something" in out
    assert "trailing" not in out
