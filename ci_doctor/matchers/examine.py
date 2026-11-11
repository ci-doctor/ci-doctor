"""`ci-doctor matchers examine`: one matcher on one log, with the real engine, read as `verify` reads it.

There is no browser playground: it would need a second engine kept equal to this one.
This shows what each output selects, its anchors marked, and which variant fired.
"""

from dataclasses import dataclass

from ci_doctor.engine import Output, Spec, Window, compile_plan, run, select
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


def _marked(lines: list[str], windows: list[Window]) -> list[str]:
    """One output's selection as `verify` renders it, with `> ` before each anchor line.

    Args:
        lines: The log.
        windows: The output's windows.

    Returns:
        The selection, or [] when the output didn't fire.
    """
    if not windows:
        return []
    anchors = {w.anchor for w in windows}
    evidence = select(lines, windows, tail_lines=0)
    return [
        ("> " if src in anchors else "  ") + line
        for line, src in zip(evidence.lines, evidence.sources, strict=True)
    ]


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
    windows = sorted(run(plan, lines), key=lambda w: w.anchor)
    out = []
    for o, op in enumerate(plan.outputs):
        own = [w for w in windows if w.output == o]
        variants = list(dict.fromkeys(w.variant for w in own if w.variant))
        out.append(Examined(op.output, variants, _marked(lines, own)))
    return out
