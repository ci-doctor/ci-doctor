"""Evaluate a match tree over the whole log at once: the anchor lines of one variant."""

from ci_doctor.engine.plan import AllOf, AnyOf, FollowedBy, Inside, Leaf, Node, Not, PrecededBy
from ci_doctor.engine.run import relations
from ci_doctor.engine.run.lines import Lines


class Evaluator:
    """Turns match trees into line sets for one log, remembering every subtree it solved.

    Equal subtrees are equal frozen dataclasses, so a regex shared by two outputs, or a
    relation repeated across variants, is evaluated once per log.
    """

    def __init__(self, hits: list[frozenset[int]], n: int) -> None:
        """Start from the scan's result.

        Args:
            hits: For each leaf, the lines it matches (`scan`).
            n: How many lines the log has.
        """
        self.hits = hits
        self.n = n
        self._memo: dict[Node, Lines] = {}

    def lines(self, node: Node) -> Lines:
        """The lines where `node` holds.

        Args:
            node: A match tree.

        Returns:
            Its line set.
        """
        if node not in self._memo:
            self._memo[node] = self._eval(node)
        return self._memo[node]

    def sorted(self, node: Node) -> list[int]:
        """The lines where `node` holds, ascending.

        Args:
            node: A match tree.

        Returns:
            Line indexes.
        """
        return self.lines(node).sorted(self.n)

    def _eval(self, node: Node) -> Lines:
        match node:
            case Leaf(index):
                return Lines(self.hits[index])
            case AllOf(items):
                out = self.lines(items[0])
                for item in items[1:]:
                    out &= self.lines(item)
                return out
            case AnyOf(items):
                out = self.lines(items[0])
                for item in items[1:]:
                    out |= self.lines(item)
                return out
            case Not(item):
                return ~self.lines(item)
            case PrecededBy(inner, within):
                return relations.preceded_by(self.sorted(inner), within, self.n)
            case FollowedBy(inner, within):
                return relations.followed_by(self.sorted(inner), within, self.n)
            case Inside(start, end, max_lines):
                return relations.inside(self.sorted(start), self.sorted(end), max_lines, self.n)
        raise TypeError(f"not a match node: {node!r}")
