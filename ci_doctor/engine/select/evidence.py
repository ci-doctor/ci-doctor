"""Windows → evidence: add the tail, merge, shed to the budget, render the gaps."""

from ci_doctor.engine.run.windows import Window
from ci_doctor.engine.select.merge import merge
from ci_doctor.engine.select.render import render
from ci_doctor.engine.select.shed import shed

#: The tail window ranks below every matcher.
TAIL = -1


def select(
    lines: list[str], windows: list[Window], tail_lines: int, max_tokens: int | None = None
) -> list[str]:
    """Reduce a log to the lines worth showing.

    Args:
        lines: The denoised log lines.
        windows: What `run` anchored in them.
        tail_lines: How many trailing lines to always keep, ranked below every
            matcher. Pass 0 to test a matcher in isolation — otherwise the tail masks
            a matcher that never fired.
        max_tokens: Evidence budget. When given, the lowest-ranked windows are
            dropped before rendering rather than left for `budget.fit` to cut
            blindly off the head. None keeps every window, whatever it costs.

    Returns:
        The selected lines with "… [N lines elided] …" markers where content was
        dropped. Falls back to every line when nothing matched and there is no tail.
    """
    wins = [Window(w.start, w.end, w.rank) for w in windows]  # merge mutates; keep the caller's intact
    if tail_lines > 0 and lines:
        wins.append(Window(max(0, len(lines) - tail_lines), len(lines), TAIL))
    merged = merge(wins)
    if not merged:
        return list(lines)
    if max_tokens is not None:
        merged = shed(lines, merged, max_tokens)
    return render(lines, merged)
