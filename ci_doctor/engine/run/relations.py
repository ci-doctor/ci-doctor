"""`preceded_by`, `followed_by` and `inside` as linear sweeps over sorted line lists."""

from bisect import bisect_right

from ci_doctor.engine.run.lines import Lines


def _spans(spans: list[tuple[int, int]], n: int) -> Lines:
    """The lines covered by inclusive `(first, last)` spans, clamped to the log.

    Args:
        spans: Spans in ascending `first` order, possibly overlapping.
        n: How many lines the log has.

    Returns:
        The covered lines, each counted once.
    """
    out: set[int] = set()
    reach = -1
    for first, last in spans:
        first, last = max(first, reach + 1, 0), min(last, n - 1)
        if first <= last:
            out.update(range(first, last + 1))
            reach = last
    return Lines(frozenset(out))


def preceded_by(hits: list[int], within: int, n: int) -> Lines:
    """Lines with a hit among the `within` lines above them.

    Args:
        hits: Lines where the inner condition holds, ascending.
        within: How far up to look.
        n: How many lines the log has.

    Returns:
        The lines where the relation holds.
    """
    return _spans([(k + 1, k + within) for k in hits], n)


def followed_by(hits: list[int], within: int, n: int) -> Lines:
    """Lines with a hit among the `within` lines below them.

    Args:
        hits: Lines where the inner condition holds, ascending.
        within: How far down to look.
        n: How many lines the log has.

    Returns:
        The lines where the relation holds.
    """
    return _spans([(k - within, k - 1) for k in hits], n)


def blocks(starts: list[int], ends: list[int], max_lines: int | None, n: int) -> list[tuple[int, int]]:
    """Non-nesting blocks: open on a start, close on the first end after it, or after `max_lines`.

    The scan resumes after a block closes, so a start inside an open block opens nothing.

    Args:
        starts: Lines that open a block, ascending.
        ends: Lines that close one, ascending.
        max_lines: The most lines a block holds; None for no cap (to the end of the log).
        n: How many lines the log has.

    Returns:
        Inclusive `(first, last)` spans in log order.
    """
    out = []
    resume = 0
    for s in starts:
        if s < resume:
            continue
        k = bisect_right(ends, s)
        last = ends[k] if k < len(ends) else n - 1
        if max_lines is not None:
            last = min(last, s + max_lines - 1)
        last = min(last, n - 1)
        out.append((s, last))
        resume = last + 1
    return out


def inside(starts: list[int], ends: list[int], max_lines: int, n: int) -> Lines:
    """Lines inside a block, both ends included.

    Args:
        starts: Lines that open a block, ascending.
        ends: Lines that close one, ascending.
        max_lines: The most lines a block holds.
        n: How many lines the log has.

    Returns:
        The lines in some block.
    """
    return _spans(blocks(starts, ends, max_lines, n), n)
