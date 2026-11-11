"""Emit the selected lines with a visible marker for every gap (invariant #6)."""

from dataclasses import dataclass

from ci_doctor.engine.run.windows import Window


@dataclass(frozen=True)
class Evidence:
    """The lines the report sees, and where each came from.

    Attributes:
        lines: Selected log lines and elision markers, in log order.
        sources: For each of `lines`, its index in the log; None for a marker.
        truncated: Whether `budget.fit` cut lines to fit the token budget.
    """

    lines: list[str]
    sources: list[int | None]
    truncated: bool = False


def render(lines: list[str], windows: list[Window]) -> Evidence:
    """Emit the windowed lines, announcing every gap between them.

    Args:
        lines: The full log.
        windows: Merged, sorted, non-overlapping windows.

    Returns:
        Selected lines interleaved with explicit elision markers. Invariant #6:
        no cut is ever silent.
    """
    out: list[str] = []
    sources: list[int | None] = []
    prev_end = 0
    for w in [*windows, Window(len(lines), len(lines), 0)]:
        if w.start > prev_end:
            out.append(f"… [{w.start - prev_end} lines elided] …")
            sources.append(None)
        out.extend(lines[w.start : w.end])
        sources.extend(range(w.start, w.end))
        prev_end = w.end
    return Evidence(out, sources)
