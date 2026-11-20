"""Token budgeting. Keeps the evidence under the model's input limit.

The blamed slice is already small after denoise+extract; this is the last-resort
cap. When it still overflows we keep the head and the tail and elide the middle
with a visible marker — never a silent truncation.
"""

from ci_doctor.engine.select.render import Evidence


def estimate_tokens(text: str) -> int:
    """Approximate a token count without loading a tokenizer.

    Args:
        text: Any text.

    Returns:
        Estimated tokens, at least 1.
    """
    # ponytail: ~4 chars/token heuristic; swap for a local tokenizer if precision matters.
    return max(1, len(text) // 4)


def fit(evidence: Evidence, max_tokens: int) -> Evidence:
    """Trim evidence to a token budget, keeping its head and its tail.

    `shed` has already dropped the lowest-ranked windows, so this only cuts inside
    one oversized window. Its head holds the anchor and the first error (often the
    root cause); its tail holds how the run ended. The middle goes, and the cut is
    always announced in the returned lines, never silent.

    Args:
        evidence: The selection, in log order.
        max_tokens: The budget from `llm.max_input_tokens`.

    Returns:
        The evidence, unchanged when it fits. Otherwise its head (a third of the
        budget) and its tail, around a visible "… [N lines elided to fit token
        budget] …" marker, with `truncated` set.
    """
    lines = evidence.lines
    if estimate_tokens("\n".join(lines)) <= max_tokens:
        return evidence
    costs = [estimate_tokens(line) + 1 for line in lines]
    head = _fitting(costs, max_tokens // 3)
    tail = _fitting(costs[head:][::-1], max_tokens - sum(costs[:head]))
    rest = len(lines) - tail
    return Evidence(
        [*lines[:head], f"… [{rest - head} lines elided to fit token budget] …", *lines[rest:]],
        [*evidence.sources[:head], None, *evidence.sources[rest:]],
        truncated=True,
    )


def _fitting(costs: list[int], budget: int) -> int:
    """How many of `costs`, taken from the front, fit in `budget`."""
    total = 0
    for n, cost in enumerate(costs):
        total += cost
        if total > budget:
            return n
    return len(costs)
