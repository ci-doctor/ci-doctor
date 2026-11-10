"""Emit the selected lines with a visible marker for every gap (invariant #6)."""

from ci_doctor.engine.run.windows import Window


def render(lines: list[str], windows: list[Window]) -> list[str]:
    """Emit the windowed lines, announcing every gap between them.

    Args:
        lines: The full log.
        windows: Merged, sorted, non-overlapping windows.

    Returns:
        Selected lines interleaved with explicit elision markers. Invariant #6:
        no cut is ever silent.
    """
    out: list[str] = []
    prev_end = 0
    for w in windows:
        if w.start > prev_end:
            out.append(f"… [{w.start - prev_end} lines elided] …")
        out.extend(lines[w.start : w.end])
        prev_end = w.end
    if prev_end < len(lines):
        out.append(f"… [{len(lines) - prev_end} lines elided] …")
    return out
