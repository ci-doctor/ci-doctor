"""Builders shared by the engine tests."""

from ci_doctor.engine import MatcherV1, Spec, compile_plan, estimate_tokens, fired, run, select


def rx(regex: str, *, id: str = "x", role: str = "tool", before: int = 0, after: int = 0) -> MatcherV1:
    """A one-output matcher anchored on `regex`, keeping `before`/`after` lines."""
    window = {k: v for k, v in (("before", before), ("after", after)) if v}
    return v1(id=id, role=role, match={"regex": regex}, **({"window": window} if window else {}))


def block(start: str, end: str, *, id: str = "x") -> MatcherV1:
    """A one-output matcher keeping the block from `start` to the next `end`."""
    return v1(id=id, match={"regex": start}, window={"until": {"regex": end}, "max_lines": 500})


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


def v1(**kw: object) -> MatcherV1:
    """A one-output schema 1 matcher with every required field filled, overridden by kwargs."""
    base = {
        "schema": 1,
        "id": "x",
        "description": "A test matcher with a long enough description.",
        "tags": ["t"],
    }
    return MatcherV1.model_validate(base | kw)
