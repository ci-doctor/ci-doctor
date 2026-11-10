"""Drop the least valuable windows until the selection fits the budget."""

from ci_doctor.engine.run.windows import Window
from ci_doctor.engine.select.budget import estimate_tokens

#: Upper bound on one `… [N lines elided] …` marker plus its line break.
MARKER_TOKENS = estimate_tokens("… [9999999 lines elided] …") + 1


def shed(lines: list[str], windows: list[Window], max_tokens: int) -> list[Window]:
    """Shed the least valuable windows until the selection fits the budget.

    Without this the budget is enforced only by `budget.fit`, which keeps the
    *tail*. That picks wrong whenever a tool reports the cause before its own
    epilogue — `npm ERR!` always trails the compiler errors that caused it, so
    tail-keep discards the diagnosis and keeps the complaint.

    Args:
        lines: The denoised log lines.
        windows: Merged, non-overlapping windows.
        max_tokens: Budget for the selected lines.

    Returns:
        The surviving windows, back in log order. Always at least one — a single
        window can exceed the budget on its own (an unbounded `start`/`end` block
        over a large suite), and truncating *inside* it is `budget.fit`'s job.
    """
    # Best first: highest rank, then latest — a job stops at its first failing command.
    # ponytail: within one compiler's output the first error is the root and is shed first;
    # fixing it needs command boundaries the log lines do not carry.
    best_first = sorted(windows, key=lambda w: (w.rank, w.start), reverse=True)
    kept: list[Window] = []
    total = MARKER_TOKENS  # n windows leave at most n + 1 gaps, so one marker up front
    for w in best_first:
        total += estimate_tokens("\n".join(lines[w.start : w.end])) + MARKER_TOKENS
        if kept and total > max_tokens:
            break
        kept.append(w)
    return sorted(kept, key=lambda w: w.start)
