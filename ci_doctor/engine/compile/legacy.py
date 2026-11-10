"""The legacy front end: today's `pattern` / `start`+`end` matcher → Plan.

- `pattern` is a leaf; `exclude` wraps it: `all: [pattern, not: any: [...]]`.
- `start`/`end` is a `start` anchor with a window `until: end` and no line cap: a
  block whose end never comes runs to the end of the log, as it always has.
- The output id is the matcher id; `role` is unchanged.
"""

from ci_doctor.engine.compile.leaves import Leaves
from ci_doctor.engine.plan import AllOf, AnyOf, Node, Not, Output, OutputPlan, VariantPlan, WindowPlan
from ci_doctor.engine.spec.legacy import MatcherConfig


def compile_legacy(m: MatcherConfig, leaves: Leaves) -> OutputPlan:
    """Compile one legacy matcher into its single output.

    Args:
        m: The validated matcher.
        leaves: The Plan's leaf table, shared across matchers.

    Returns:
        One output with one unnamed variant.
    """
    anchor_rx = m.pattern or m.start
    if anchor_rx is None:  # unreachable: MatcherConfig requires one form
        raise ValueError(f"matcher {m.id!r} has no anchor")
    match: Node = leaves(anchor_rx)
    if m.exclude:
        match = AllOf((match, Not(AnyOf(tuple(leaves(rx) for rx in m.exclude)))))
    if m.pattern:
        window = WindowPlan(before=m.before, after=m.after)
    else:
        window = WindowPlan(until=leaves(m.end or ""), max_lines=None)
    return OutputPlan(Output(m.id, m.role, m.description), (VariantPlan("", match, window),))
