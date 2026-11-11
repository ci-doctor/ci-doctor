"""Token budgeting: fits under the cap, keeps the tail, never truncates silently."""

from ci_doctor.engine import Evidence, estimate_tokens, fit


def _evidence(lines: list[str]) -> Evidence:
    return Evidence(lines, list(range(len(lines))))


def test_fit_noop_when_under_budget():
    """Evidence already under budget is returned untouched."""
    ev = _evidence(["short line"] * 3)
    assert fit(ev, max_tokens=1000) == ev


def test_fit_keeps_tail_and_marks_elision():
    """Over budget, the head goes, the tail stays, the cut is announced, and sources follow."""
    lines = [f"line number {i} with some content here" for i in range(1000)]
    out = fit(_evidence(lines), max_tokens=50)
    assert out.truncated is True
    assert out.lines[0].startswith("… [") and "elided to fit token budget" in out.lines[0]
    assert out.lines[-1] == lines[-1]  # tail (where the failure lives) is kept
    assert estimate_tokens("\n".join(out.lines[1:])) <= 50  # actually fits, no silent overflow
    assert out.sources[0] is None and out.sources[-1] == 999
