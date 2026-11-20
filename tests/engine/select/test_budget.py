"""Token budgeting: fits under the cap, keeps head and tail, never truncates silently."""

from ci_doctor.engine import Evidence, estimate_tokens, fit


def _evidence(lines: list[str]) -> Evidence:
    return Evidence(lines, list(range(len(lines))))


def test_fit_noop_when_under_budget():
    """Evidence already under budget is returned untouched."""
    ev = _evidence(["short line"] * 3)
    assert fit(ev, max_tokens=1000) == ev


def test_fit_keeps_head_and_tail_and_marks_elision():
    """Over budget, the middle goes: the head holds the anchor, the tail the end of the run."""
    lines = [f"line number {i} with some content here" for i in range(1000)]
    out = fit(_evidence(lines), max_tokens=60)
    assert out.truncated is True
    marker = next(i for i, line in enumerate(out.lines) if "elided to fit token budget" in line)
    assert out.lines[0] == lines[0]  # the anchor of an oversized window survives
    assert out.lines[-1] == lines[-1]
    kept = out.lines[:marker] + out.lines[marker + 1 :]
    assert estimate_tokens("\n".join(kept)) <= 60  # actually fits, no silent overflow
    assert out.sources[marker] is None and out.sources[0] == 0 and out.sources[-1] == 999
