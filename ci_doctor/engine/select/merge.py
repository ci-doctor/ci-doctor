"""Overlapping windows merge, keeping the highest rank."""

from ci_doctor.engine.run.windows import Window


def merge(wins: list[Window]) -> list[Window]:
    """Collapse overlapping or adjacent windows into contiguous ones.

    Args:
        wins: Windows in any order. Mutated in place while merging.

    Returns:
        Non-overlapping windows sorted by start, each carrying the highest
        rank among those it absorbed.
    """
    if not wins:
        return []
    wins = sorted(wins, key=lambda w: w.start)
    merged = [wins[0]]
    for w in wins[1:]:
        last = merged[-1]
        if w.start <= last.end:  # overlapping or adjacent
            last.end = max(last.end, w.end)
            last.rank = max(last.rank, w.rank)
        else:
            merged.append(w)
    return merged
