"""L2 · spec → Plan: every schema version's front end compiles to the one Plan."""

from collections.abc import Collection, Sequence

from ci_doctor.engine.compile.leaves import Leaves
from ci_doctor.engine.compile.v1 import compile_v1
from ci_doctor.engine.plan import OutputPlan, Plan
from ci_doctor.engine.spec import Spec


def compile_plan(specs: Sequence[Spec], exclude: Collection[str] = ()) -> Plan:
    """Compile matchers of any known schema version into one Plan.

    Args:
        specs: Validated matchers, in load order.
        exclude: Output ids to leave out (`matchers.exclude`); their regexes are never scanned.

    Returns:
        The Plan: their outputs in order, sharing one leaf table.

    Raises:
        CompileError: Naming the matcher, the place in it, and what is wrong.
    """
    leaves = Leaves()
    outputs: list[OutputPlan] = []
    for spec in specs:
        outputs += compile_v1(spec, leaves, exclude)
    return Plan(leaves.table(), tuple(outputs))
