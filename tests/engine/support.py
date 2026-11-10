"""Builders shared by the engine tests."""

from ci_doctor.engine import MatcherConfig, estimate_tokens, extract


def matcher(**kw: object) -> MatcherConfig:
    """Build a MatcherConfig with every field defaulted, overridden by kwargs."""
    return MatcherConfig(**{"id": "x", **kw})


def budgeted(lines: list[str], matchers: list[MatcherConfig]) -> str:
    """Extract at half the cost of the whole log, so exactly one of two windows fits."""
    return "\n".join(
        extract(lines, matchers, tail_lines=0, max_tokens=estimate_tokens("\n".join(lines)) // 2)
    )
