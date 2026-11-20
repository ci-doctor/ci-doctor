"""Windows → evidence: add the tail, merge, shed to the budget, render the gaps."""

from ci_doctor.engine.run.windows import TAIL, Window
from ci_doctor.engine.select.merge import merge
from ci_doctor.engine.select.render import Evidence, render
from ci_doctor.engine.select.shed import shed


def select(
    lines: list[str], windows: list[Window], tail_lines: int, max_tokens: int | None = None
) -> Evidence:
    """Reduce a log to the lines worth showing.

    Args:
        lines: The denoised log lines.
        windows: What `run` anchored in them.
        tail_lines: How many trailing lines to always keep, ranked below every
            matcher. Pass 0 to test a matcher in isolation — otherwise the tail masks
            a matcher that never fired.
        max_tokens: Evidence budget. When the selection is over it, condensed
            windows drop their other lines, then the lowest-ranked windows are
            dropped, rather than left for `budget.fit` to cut. None keeps every
            window whole, whatever it costs.

    Returns:
        The selected lines with "… [N lines elided] …" markers where content was
        dropped, and where each came from. Every line when nothing matched and
        there is no tail.
    """
    # merge mutates; keep the caller's intact
    wins = [Window(w.start, w.end, w.rank, keep=w.keep) for w in windows]
    if tail_lines > 0 and lines:
        wins.append(Window(max(0, len(lines) - tail_lines), len(lines), TAIL))
    merged = merge(wins)
    if not merged:
        return Evidence(list(lines), list(range(len(lines))))
    return render(lines, shed(lines, merged, max_tokens))
