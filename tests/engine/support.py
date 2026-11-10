"""Builders shared by the engine tests."""

from ci_doctor.engine import MatcherConfig, Spec, compile_plan, estimate_tokens, fired, run, select


def matcher(**kw: object) -> MatcherConfig:
    """Build a MatcherConfig with every field defaulted, overridden by kwargs."""
    return MatcherConfig(**{"id": "x", **kw})


def extract(
    lines: list[str], matchers: list[Spec], tail_lines: int, max_tokens: int | None = None
) -> list[str]:
    """Compile, run and select in one call: the path `build_bundle` takes."""
    return select(lines, run(compile_plan(matchers), lines), tail_lines, max_tokens)


def fired_ids(lines: list[str], matchers: list[Spec]) -> list[str]:
    """The ids of the outputs anchored in `lines`, in log order."""
    return [o.id for o in fired(compile_plan(matchers), lines)]


def budgeted(lines: list[str], matchers: list[Spec]) -> str:
    """Extract at half the cost of the whole log, so exactly one of two windows fits."""
    return "\n".join(
        extract(lines, matchers, tail_lines=0, max_tokens=estimate_tokens("\n".join(lines)) // 2)
    )
