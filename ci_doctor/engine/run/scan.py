"""One RE2 pass over the log: for each leaf regex, the lines it matches."""

from ci_doctor.engine.plan import Plan
from ci_doctor.regex import pattern_set


def scan(plan: Plan, lines: list[str]) -> list[frozenset[int]]:
    """Match every leaf of every output against each line, in one RE2 set.

    One pass per line, not one per pattern — see `ci_doctor/regex.py` for why that
    matters at a hundred matchers.

    Args:
        plan: The compiled matchers.
        lines: The denoised log lines.

    Returns:
        For each leaf of `plan.leaves`, the line indexes it matches.
    """
    match = pattern_set(plan.leaves)
    hits: list[list[int]] = [[] for _ in plan.leaves]
    for i, line in enumerate(lines):
        for leaf in match(line):
            hits[leaf].append(i)
    return [frozenset(h) for h in hits]
