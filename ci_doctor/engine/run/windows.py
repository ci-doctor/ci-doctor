"""Run matchers over a log: every anchor becomes a window carrying its role's rank.

Anchored windows catch causes that print *before* a framework's summary, so a
tail-only view would miss them.
"""

from dataclasses import dataclass

from ci_doctor.engine.spec.legacy import MatcherConfig
from ci_doctor.engine.spec.roles import ROLE_RANK
from ci_doctor.regex import pattern_set

#: Shared by every line no pattern hits, so a long log costs no set per line.
_NO_HITS: frozenset[int] = frozenset()


@dataclass
class Window:
    """A half-open slice of the log worth keeping.

    Attributes:
        start: First line index, inclusive.
        end: Last line index, exclusive.
        rank: `ROLE_RANK` of the matcher that produced it, `TAIL` for the tail.
            Survives merging as the max of the merged windows, so a tool window
            never loses rank by touching a wrapper's.
    """

    start: int
    end: int  # exclusive
    rank: int


def windows_for(lines: list[str], matchers: list[MatcherConfig]) -> list[Window]:
    """Run every matcher over the log and collect the windows they anchor.

    Every regex of every matcher goes into one RE2 set, so each line is matched
    once, not once per pattern — see `ci_doctor/regex.py` for why that matters.

    Args:
        lines: The denoised log lines.
        matchers: The evidence matchers, loaded from the configured registries.

    Returns:
        One window per hit, unsorted and possibly overlapping. Empty when nothing
        matched — which is why a pack that never fires fails *silently*, and why
        each one needs positive fixtures under `ci-doctor matchers verify`.
    """
    regexes: list[str] = []

    def add(rx: str) -> int:
        regexes.append(rx)
        return len(regexes) - 1

    plans: list[tuple[MatcherConfig, int, set[int], int | None]] = []
    for m in matchers:
        exclude = {add(rx) for rx in m.exclude}
        if m.pattern:
            plans.append((m, add(m.pattern), exclude, None))
        elif m.start and m.end:
            plans.append((m, add(m.start), exclude, add(m.end)))
    match = pattern_set(regexes)
    hits = [frozenset(h) if (h := match(line)) else _NO_HITS for line in lines]

    wins: list[Window] = []
    for m, anchor, exclude, end in plans:
        rank = ROLE_RANK[m.role]
        anchored = [i for i, hit in enumerate(hits) if anchor in hit and not hit & exclude]
        if end is None:
            wins += [Window(max(0, i - m.before), min(len(lines), i + m.after + 1), rank) for i in anchored]
            continue
        resume = 0  # a block's scan resumes after its end, so blocks never nest
        for i in anchored:
            if i < resume:
                continue
            j = next((k for k in range(i + 1, len(lines)) if end in hits[k]), len(lines))
            resume = min(len(lines), j + 1)
            wins.append(Window(i, resume, rank))
    return wins
