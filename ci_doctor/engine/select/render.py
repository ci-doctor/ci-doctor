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
        windows: Merged, sorted, non-overlapping windows; a condensed one emits only its kept runs.

    Returns:
        Selected lines interleaved with explicit elision markers. Invariant #6:
        no cut is ever silent.
    """
    out: list[str] = []
    sources: list[int | None] = []
    prev_end = 0
    for start, end in [p for w in windows for p in w.pieces()] + [(len(lines), len(lines))]:
        if start > prev_end:
            out.append(f"… [{start - prev_end} lines elided] …")
            sources.append(None)
        out.extend(lines[start:end])
        sources.extend(range(start, end))
        prev_end = end
    return Evidence(out, sources)
