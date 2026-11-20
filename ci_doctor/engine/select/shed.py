"""Drop the least valuable windows until the selection fits the budget."""

from dataclasses import replace

from ci_doctor.engine.run.windows import Window
from ci_doctor.engine.select.budget import estimate_tokens

#: Upper bound on one `… [N lines elided] …` marker plus its line break.
MARKER_TOKENS = estimate_tokens("… [9999999 lines elided] …") + 1


def shed(lines: list[str], windows: list[Window], max_tokens: int | None) -> list[Window]:
    """Condense, then shed the least valuable windows, until the selection fits the budget.

    Without this the budget is enforced only by `budget.fit`, which cuts inside one
    stretch of lines. That picks wrong whenever a tool reports the cause before its own
    epilogue — `npm ERR!` always trails the compiler errors that caused it, so a blind
    cut discards the diagnosis and keeps the complaint.

    Args:
        lines: The denoised log lines.
        windows: Merged, non-overlapping windows.
        max_tokens: Budget for the selected lines; None for no budget.

    Returns:
        Every window whole when they all fit (or there is no budget). Otherwise the
        windows condensed to their kept lines, and the most valuable that fit, back
        in log order. Always at least one — a single window can exceed the budget
        on its own (an unbounded `start`/`end` block over a large suite), and
        truncating *inside* it is `budget.fit`'s job.
    """
    whole = [replace(w, keep=None) for w in windows]
    if max_tokens is None or _cost(lines, whole) <= max_tokens:
        return whole
    # Best first: highest rank, then latest — a job stops at its first failing command.
    # ponytail: within one compiler's output the first error is the root and is shed first;
    # fixing it needs command boundaries the log lines do not carry.
    best_first = sorted(windows, key=lambda w: (w.rank, w.start), reverse=True)
    kept: list[Window] = []
    total = MARKER_TOKENS  # n pieces leave at most n + 1 gaps, so one marker up front
    for w in best_first:
        total += _cost(lines, [w]) - MARKER_TOKENS
        if kept and total > max_tokens:
            break
        kept.append(w)
    return sorted(kept, key=lambda w: w.start)


def _cost(lines: list[str], windows: list[Window]) -> int:
    """Tokens the windows render to: their kept pieces, and a marker before each and after the last."""
    pieces = [p for w in windows for p in w.pieces()]
    text = sum(estimate_tokens("\n".join(lines[a:b])) for a, b in pieces)
    return text + MARKER_TOKENS * (len(pieces) + 1)
