"""Token budgeting. Keeps the evidence under the model's input limit.

The blamed slice is already small after denoise+extract; this is the last-resort
cap. When it still overflows we keep the tail (the failure lives at the end) and
elide the head with a visible marker — never a silent truncation.
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
    """Trim evidence to a token budget, keeping the tail.

    The failure lives at the end of a log, so the head is what gets dropped — and
    the drop is always announced in the returned lines, never silent. The
    lowest-ranked windows are already shed upstream by `shed.shed`, so this only
    ever cuts *inside* the evidence that survived that pass.

    Args:
        evidence: The selection, in log order.
        max_tokens: The budget from `llm.max_input_tokens`.

    Returns:
        The evidence, unchanged when it fits. Otherwise its tail, after a visible
        "… [N lines elided to fit token budget] …" marker, with `truncated` set.

    ponytail: tail-keep within the surviving window. Upgrade to phase-aware
    70/10/20 if the tail heuristic still misses causes.
    """
    lines = evidence.lines
    if estimate_tokens("\n".join(lines)) <= max_tokens:
        return evidence
    kept = 0
    total = 0
    for line in reversed(lines):
        cost = estimate_tokens(line) + 1
        if total + cost > max_tokens:
            break
        kept += 1
        total += cost
    dropped = len(lines) - kept
    return Evidence(
        [f"… [{dropped} lines elided to fit token budget] …", *lines[dropped:]],
        [None, *evidence.sources[dropped:]],
        truncated=True,
    )
