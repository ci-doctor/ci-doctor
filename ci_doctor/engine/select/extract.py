"""Select the lines worth showing: a tail window plus anchored context windows.

Overlapping windows merge. Every gap between selected windows is marked with an
explicit elision count — never a silent cut.
"""

from ci_doctor.engine.run.windows import Window, windows_for
from ci_doctor.engine.select.merge import merge
from ci_doctor.engine.select.render import render
from ci_doctor.engine.select.shed import shed
from ci_doctor.engine.spec.legacy import MatcherConfig

#: The tail window ranks below every matcher.
TAIL = -1


def extract(
    lines: list[str], matchers: list[MatcherConfig], tail_lines: int, max_tokens: int | None = None
) -> list[str]:
    """Reduce a log to the lines worth showing.

    Args:
        lines: The denoised log lines.
        matchers: The evidence matchers, loaded from the configured registries.
        tail_lines: How many trailing lines to always keep, ranked below every
            matcher. Pass 0 to test a pack in isolation — otherwise the tail masks
            a matcher that never fired.
        max_tokens: Evidence budget. When given, the lowest-ranked windows are
            dropped before rendering rather than left for `budget.fit` to cut
            blindly off the head. None keeps every window, whatever it costs.

    Returns:
        The selected lines with "… [N lines elided] …" markers where content was
        dropped. Falls back to every line when nothing matched and there is no tail.
    """
    wins = windows_for(lines, matchers)
    if tail_lines > 0 and lines:
        wins.append(Window(max(0, len(lines) - tail_lines), len(lines), TAIL))
    merged = merge(wins)
    if not merged:
        return list(lines)
    if max_tokens is not None:
        merged = shed(lines, merged, max_tokens)
    return render(lines, merged)
