"""Which outputs have an anchor in the final evidence, in log order."""

from ci_doctor.engine.plan import Output, Plan
from ci_doctor.engine.run.windows import Window
from ci_doctor.engine.select.render import Evidence


def fired(plan: Plan, windows: list[Window], evidence: Evidence) -> list[Output]:
    """Name the outputs whose anchor line survived every cut.

    Read from where each evidence line came from, not by matching again: the
    evidence has lost the context that relations were evaluated against.

    Args:
        plan: The compiled matchers `windows` came from.
        windows: What `run` anchored in the full log.
        evidence: The evidence as it will be sent.

    Returns:
        The outputs with an anchor in `evidence`, ordered by their first such anchor.
    """
    kept = set(evidence.sources)
    first: dict[int, int] = {}
    for w in sorted(windows, key=lambda w: w.anchor):
        if w.anchor in kept:
            first.setdefault(w.output, w.anchor)
    return [plan.outputs[o].output for o in first]
