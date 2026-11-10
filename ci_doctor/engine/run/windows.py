"""Plan × lines → windows: every anchor of every variant, with the lines it keeps.

Anchored windows catch causes that print *before* a framework's summary, so a
tail-only view would miss them.
"""

from bisect import bisect_right
from dataclasses import dataclass

from ci_doctor.engine.plan import Plan, WindowPlan
from ci_doctor.engine.run.anchors import Evaluator
from ci_doctor.engine.run.relations import blocks
from ci_doctor.engine.run.scan import scan
from ci_doctor.engine.spec.roles import RANK


@dataclass
class Window:
    """A half-open slice of the log worth keeping.

    Attributes:
        start: First line index, inclusive.
        end: Last line index, exclusive.
        rank: `RANK` of the output that produced it, `TAIL` for the tail.
            Survives merging as the max of the merged windows, so a tool window
            never loses rank by touching a wrapper's.
        output: Index into `Plan.outputs`; -1 for the tail.
        anchor: The line that anchored it; -1 for the tail.
        variant: The variant that anchored it.
    """

    start: int
    end: int  # exclusive
    rank: int
    output: int = -1
    anchor: int = -1
    variant: str = ""


def _downwards(ev: Evaluator, anchors: list[int], window: WindowPlan) -> list[tuple[int, int]]:
    """Each kept anchor and the last line its window reaches.

    Args:
        ev: The log's evaluator.
        anchors: The anchor lines, ascending.
        window: What to keep below each.

    Returns:
        `(anchor, last)` pairs, `last` inclusive. An anchor inside an earlier `until` /
        `while` window of the same variant is skipped, so those windows never nest.
    """
    n = ev.n
    if window.until is not None:
        return blocks(anchors, ev.sorted(window.until), window.max_lines, n)
    if window.while_ is None:
        return [(i, min(n - 1, i + window.after)) for i in anchors]
    cont, cap = ev.lines(window.while_), window.max_lines or n
    out, resume = [], 0
    for i in anchors:
        if i < resume:
            continue
        last = i
        while last + 1 < n and last + 2 - i <= cap and last + 1 in cont:
            last += 1
        out.append((i, last))
        resume = last + 1
    return out


def _upwards(ev: Evaluator, anchor: int, window: WindowPlan) -> int:
    """The first line an anchor's window keeps.

    Args:
        ev: The log's evaluator.
        anchor: The anchor line.
        window: What to keep above it.

    Returns:
        The nearest `from_` line within reach (the anchor itself when none), or
        `before` lines up, clamped to the log.
    """
    if window.from_ is None:
        return max(0, anchor - window.before)
    heads = ev.sorted(window.from_)
    k = bisect_right(heads, anchor) - 1
    return heads[k] if k >= 0 and heads[k] >= anchor - window.from_within else anchor


def run(plan: Plan, lines: list[str]) -> list[Window]:
    """Find every anchor of every output and the window it keeps.

    Args:
        plan: The compiled matchers.
        lines: The denoised log lines.

    Returns:
        One window per anchor, unsorted and possibly overlapping. Empty when nothing
        matched — which is why a matcher that never fires fails *silently*, and why
        each one needs positive fixtures under `ci-doctor matchers verify`.
    """
    if not plan.outputs or not lines:
        return []
    ev = Evaluator(scan(plan, lines), len(lines))
    wins = []
    for o, out in enumerate(plan.outputs):
        rank = RANK[out.output.role]
        for v in out.variants:
            for anchor, last in _downwards(ev, ev.sorted(v.match), v.window):
                wins.append(Window(_upwards(ev, anchor, v.window), last + 1, rank, o, anchor, v.name))
    return wins
