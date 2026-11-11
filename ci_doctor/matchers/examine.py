"""`ci-doctor matchers examine`: one matcher on one log, with the real engine, read as `verify` reads it.

There is no browser playground: it would need a second engine kept equal to this one.
This shows what each output selects, its anchors marked, and which variant fired.
"""

from dataclasses import dataclass

from ci_doctor.engine import Output, Plan, Spec, compile_plan, run
from ci_doctor.matchers.logs import Framing, read_log


@dataclass(frozen=True)
class Examined:
    """What one output selects from the log.

    Attributes:
        output: The output.
        variants: The variants that anchored, in log order of their first anchor.
        lines: The selection: `> ` before an anchor line, `  ` before context, and
            an elision marker for every gap. Empty when the output didn't fire.
    """

    output: Output
    variants: list[str]
    lines: list[str]


def _render(lines: list[str], spans: list[tuple[int, int]], anchors: set[int]) -> list[str]:
    """The selected lines, anchors marked, gaps announced.

    Args:
        lines: The log.
        spans: Half-open windows.
        anchors: Anchor line indexes.

    Returns:
        The rendered selection.
    """
    out, prev = [], 0
    for start, end in sorted(spans):
        start = max(start, prev)
        if start >= end:
            continue
        if start > prev:
            out.append(f"  … [{start - prev} lines elided] …")
        out += [("> " if i in anchors else "  ") + lines[i] for i in range(start, end)]
        prev = end
    if out and prev < len(lines):
        out.append(f"  … [{len(lines) - prev} lines elided] …")
    return out


def examine(spec: Spec, raw: str, framing: Framing) -> list[Examined]:
    """Run one matcher over one log and show what each output selects.

    Args:
        spec: The loaded matcher.
        raw: The log text.
        framing: The runner framing to strip, as for a fixture.

    Returns:
        One entry per output, in file order.
    """
    lines = read_log(raw, framing)
    plan = compile_plan([spec])
    out = []
    for op in plan.outputs:
        wins = sorted(run(Plan(plan.leaves, (op,)), lines), key=lambda w: w.anchor)
        variants = list(dict.fromkeys(w.variant for w in wins if w.variant))
        out.append(
            Examined(
                op.output,
                variants,
                _render(lines, [(w.start, w.end) for w in wins], {w.anchor for w in wins}),
            )
        )
    return out
