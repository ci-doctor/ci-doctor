"""L2 · spec → Plan: every schema version's front end compiles to the one Plan."""

from collections.abc import Collection, Sequence

from ci_doctor.engine.compile.leaves import Leaves
from ci_doctor.engine.compile.legacy import compile_legacy
from ci_doctor.engine.plan import OutputPlan, Plan
from ci_doctor.engine.spec import Spec
from ci_doctor.engine.spec.legacy import MatcherConfig


def compile_plan(specs: Sequence[Spec], exclude: Collection[str] = ()) -> Plan:
    """Compile matchers of any known schema version into one Plan.

    Args:
        specs: Validated matchers, in load order.
        exclude: Output ids to leave out (`matchers.exclude`).

    Returns:
        The Plan: their outputs in order, sharing one leaf table.

    Raises:
        CompileError: Naming the matcher, the place in it, and what is wrong.
    """
    leaves = Leaves()
    outputs: list[OutputPlan] = []
    for spec in specs:
        if isinstance(spec, MatcherConfig):
            outputs.append(compile_legacy(spec, leaves))
        else:
            from ci_doctor.engine.compile.v1 import compile_v1

            outputs += compile_v1(spec, leaves)
    return Plan(leaves.table(), tuple(o for o in outputs if o.output.id not in exclude))
