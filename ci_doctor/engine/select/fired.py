"""Which matchers have an anchor in the final evidence, in log order."""

from ci_doctor.engine.run.windows import windows_for
from ci_doctor.engine.spec.legacy import MatcherConfig


def fired(lines: list[str], matchers: list[MatcherConfig]) -> list[str]:
    """Name the packs that anchor in these lines — asked of the final evidence.

    Args:
        lines: The evidence as it will be sent, after every cut.
        matchers: The evidence matchers, loaded from the configured registries.

    Returns:
        Ids of the matchers with at least one anchor in `lines`, in log order.
    """
    first = {}
    for m in matchers:
        # before=0: a window then starts on its anchor, which is what log order means here
        if wins := windows_for(lines, [m.model_copy(update={"before": 0})]):
            first[m.id] = min(w.start for w in wins)
    return sorted(first, key=first.__getitem__)
