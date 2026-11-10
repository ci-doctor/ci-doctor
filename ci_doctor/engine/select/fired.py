"""Which outputs have an anchor in the final evidence, in log order."""

from ci_doctor.engine.plan import Output, Plan
from ci_doctor.engine.run.windows import run


def fired(plan: Plan, lines: list[str]) -> list[Output]:
    """Name the outputs that anchor in these lines — asked of the final evidence.

    Args:
        plan: The compiled matchers.
        lines: The evidence as it will be sent, after every cut.

    Returns:
        The outputs with at least one anchor in `lines`, ordered by their first anchor.
    """
    first: dict[int, int] = {}
    for w in run(plan, lines):
        first[w.output] = min(first.get(w.output, w.anchor), w.anchor)
    return [plan.outputs[o].output for o in sorted(first, key=lambda o: (first[o], o))]
