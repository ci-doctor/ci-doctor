"""Overlapping windows merge, keeping the highest rank."""

from ci_doctor.engine.run.windows import TAIL, Window


def _kept(w: Window) -> frozenset[int]:
    """The lines `w` keeps when a window it merges with condenses.

    All of them when it doesn't condense; none for the tail, which is a guess about
    position and shed before anything, so it can't keep what a matcher cut.
    """
    if w.keep is not None:
        return w.keep
    return frozenset() if w.rank == TAIL else frozenset(range(w.start, w.end))


def merge(wins: list[Window]) -> list[Window]:
    """Collapse overlapping or adjacent windows into contiguous ones.

    Args:
        wins: Windows in any order. Mutated in place while merging.

    Returns:
        Non-overlapping windows sorted by start, each carrying the highest
        rank among those it absorbed. A line stays condensable only if every
        window covering it condenses it away.
    """
    if not wins:
        return []
    wins = sorted(wins, key=lambda w: w.start)
    merged = [wins[0]]
    for w in wins[1:]:
        last = merged[-1]
        if w.start <= last.end:  # overlapping or adjacent
            if last.keep is not None or w.keep is not None:
                last.keep = _kept(last) | _kept(w)
            last.end = max(last.end, w.end)
            last.rank = max(last.rank, w.rank)
        else:
            merged.append(w)
    return merged
