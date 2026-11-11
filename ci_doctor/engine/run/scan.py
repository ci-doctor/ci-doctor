"""One RE2 pass over the log: for each leaf regex, the lines it matches."""

from collections.abc import Callable
from functools import lru_cache

from ci_doctor.engine.plan import Plan
from ci_doctor.lib.regex import pattern_set


@lru_cache(maxsize=8)
def _matcher(leaves: tuple[str, ...]) -> Callable[[str], list[int]]:
    """One RE2 set per leaf table, built once however many logs it scans."""
    return pattern_set(leaves)


def scan(plan: Plan, lines: list[str]) -> list[frozenset[int]]:
    """Match every leaf of every output against each line, in one RE2 set.

    One pass per line, not one per pattern — see `ci_doctor/lib/regex.py` for why that
    matters at a hundred matchers.

    Args:
        plan: The compiled matchers.
        lines: The denoised log lines.

    Returns:
        For each leaf of `plan.leaves`, the line indexes it matches.
    """
    match = _matcher(plan.leaves)
    hits: list[list[int]] = [[] for _ in plan.leaves]
    for i, line in enumerate(lines):
        for leaf in match(line):
            hits[leaf].append(i)
    return [frozenset(h) for h in hits]
