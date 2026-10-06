"""RE2 for every regex a user or a pack can supply.

Python's `re` backtracks, so one careless pattern in a `.ci-doctor.yml` can hang a
job until its timeout — and `exit 0` does not help a pipeline that never ends.
RE2 matches in linear time by construction; that is the whole ReDoS guard.

The price is per call: crossing into C++ makes one RE2 search ~5x slower than `re`
on a short log line. So a list of patterns is matched in *one* pass per line
(`pattern_set`, an `re2.Set`), which measured ~15x faster than `re` looping over
the same patterns.

Internal hardcoded patterns (segmenters, redaction defaults) stay on `re`: they are
reviewed code, not input, and some use features RE2 deliberately lacks.
"""

import functools
from collections.abc import Callable, Sequence

import re2

#: The compiled-pattern type `compile_user` returns.
Regex = re2._Regexp

_OPTS = re2.Options()
_OPTS.log_errors = False  # otherwise RE2 prints every parse error to stderr through absl


@functools.cache
def compile_user(pattern: str) -> Regex:
    r"""Compile a user-supplied pattern with RE2.

    Args:
        pattern: The regex, in RE2 syntax.

    Returns:
        The compiled pattern. Cached: config patterns are few and reused per job.

    Raises:
        ValueError: If RE2 rejects the pattern — a lookaround, a backreference,
            `\Z`, a repeat over 1000. Pydantic turns it into a validation error
            naming the config key.
    """
    try:
        return re2.compile(pattern, _OPTS)
    except re2.error as err:
        reason = err.args[0] if err.args else err
        if isinstance(reason, bytes):
            reason = reason.decode(errors="replace")
        raise ValueError(f"{pattern!r} is not valid RE2: {reason}") from None


def pattern_set(patterns: Sequence[str]) -> Callable[[str], list[int]]:
    """Compile many patterns into one matcher that reports which of them hit a line.

    Args:
        patterns: Regexes in RE2 syntax.

    Returns:
        A function mapping a line to the indices (into `patterns`) that match
        anywhere in it, in no particular order; empty when none do.

    Raises:
        ValueError: If any pattern is not valid RE2, naming that pattern.
    """
    if not patterns:
        return lambda _line: []
    rset = re2.Set.SearchSet(_OPTS)
    for p in patterns:
        compile_user(p)  # fail with the same message config validation gives
        rset.Add(p)
    rset.Compile()

    def match(line: str) -> list[int]:
        try:
            return rset.Match(line) or []
        except UnicodeEncodeError:
            # A lone surrogate has no UTF-8 form and RE2 matches bytes; shipped log
            # sources decode with "replace", so this only guards a future one.
            return rset.Match(line.encode("utf-8", "replace")) or []

    # ponytail: Set.Match reports *no* hits if RE2 exhausts its DFA memory (8 MiB
    # default). Measured fine at 1,640 patterns; raise `_OPTS.max_mem` if a rules
    # repo outgrows it.
    return match
