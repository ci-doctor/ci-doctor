"""A set of line indexes that can also stand for its complement, so `not` costs nothing.

Most lines match nothing, so sets of matching lines stay small. A `not` would make
them dense; instead it flips a flag, and `&` / `|` follow De Morgan. Only a relation
over a negated set, or a negated final answer, ever materialises the complement.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Lines:
    """The lines in `members`, or with `negated` every line *not* in it.

    Attributes:
        members: Line indexes.
        negated: Whether the set means its complement.
    """

    members: frozenset[int]
    negated: bool = False

    def __invert__(self) -> "Lines":
        """The complement."""
        return Lines(self.members, not self.negated)

    def __and__(self, other: "Lines") -> "Lines":
        """Lines in both."""
        a, b = self, other
        if not a.negated and not b.negated:
            return Lines(a.members & b.members)
        if a.negated and b.negated:
            return Lines(a.members | b.members, negated=True)
        pos, neg = (a, b) if b.negated else (b, a)
        return Lines(pos.members - neg.members)

    def __or__(self, other: "Lines") -> "Lines":
        """Lines in either."""
        return ~(~self & ~other)

    def sorted(self, n: int) -> list[int]:
        """The lines, materialised and in order.

        Args:
            n: How many lines the log has.

        Returns:
            Line indexes in `range(n)`, ascending.
        """
        if self.negated:
            return [i for i in range(n) if i not in self.members]
        return sorted(self.members)

    def __contains__(self, i: int) -> bool:
        """Whether line `i` is in the set."""
        return (i in self.members) != self.negated
