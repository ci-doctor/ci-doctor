"""The Plan's leaf table: every regex once, so the RE2 set never matches a pattern twice."""

from ci_doctor.engine.plan import Leaf


class Leaves:
    """Hands out one `Leaf` per distinct regex, in first-seen order."""

    def __init__(self) -> None:
        """Start empty."""
        self._index: dict[str, int] = {}

    def __call__(self, regex: str) -> Leaf:
        """The leaf for `regex`, added on first sight.

        Args:
            regex: An expanded, already-checked RE2 pattern.

        Returns:
            Its leaf.
        """
        return Leaf(self._index.setdefault(regex, len(self._index)))

    def table(self) -> tuple[str, ...]:
        """Every regex, indexed by its leaf."""
        return tuple(self._index)
