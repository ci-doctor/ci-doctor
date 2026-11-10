"""The Plan: the one internal form every matcher schema version compiles to.

`run` and `select` only ever see a Plan, so a new schema version is a new front
end (`spec/` + `compile/`), never a change to how matching runs. Plain frozen
dataclasses: hashable, comparable, and printable as JSON for the frozen snapshots
that pin each released schema version (invariant #11).
"""

from dataclasses import dataclass, fields, is_dataclass
from typing import Any


@dataclass(frozen=True)
class Output:
    """What the prompt and the report see of one matcher output.

    Attributes:
        id: `alias/matcher` for a one-output matcher, `alias/matcher.output` otherwise.
        role: Ranks its windows (`RANK`) and tells the model how to read them.
        description: What its evidence means; prompt text.
    """

    id: str
    role: str
    description: str | None = None


@dataclass(frozen=True)
class Leaf:
    """A line matches regex number `index` of `Plan.leaves`."""

    index: int


@dataclass(frozen=True)
class AllOf:
    """A line matches every item."""

    items: tuple["Node", ...]


@dataclass(frozen=True)
class AnyOf:
    """A line matches at least one item."""

    items: tuple["Node", ...]


@dataclass(frozen=True)
class Not:
    """A line doesn't match the item."""

    item: "Node"


@dataclass(frozen=True)
class PrecededBy:
    """A line where `node` matches one of the `within` lines above."""

    node: "Node"
    within: int


@dataclass(frozen=True)
class FollowedBy:
    """A line where `node` matches one of the `within` lines below."""

    node: "Node"
    within: int


@dataclass(frozen=True)
class Inside:
    """A line inside a block: from a `start` line to the next `end` line, both included.

    Attributes:
        start: Opens a block.
        end: Closes it, on the first match after `start`.
        max_lines: Closes it after this many lines if `end` never comes.
    """

    start: "Node"
    end: "Node"
    max_lines: int


Node = Leaf | AllOf | AnyOf | Not | PrecededBy | FollowedBy | Inside


@dataclass(frozen=True)
class WindowPlan:
    """Which lines around an anchor become evidence.

    Attributes:
        before: Lines kept above the anchor, when there's no `from_`.
        after: Lines kept below the anchor, when there's no `until` / `while_`.
        from_: Start at the nearest line above (or at) the anchor matching this.
        from_within: How far up `from_` looks.
        until: End at the next line matching this, inclusive.
        while_: Extend while the next line matches this.
        max_lines: Caps `until` / `while_`: the window holds at most this many lines
            from the anchor down. None only for the legacy `start`/`end` block, which
            runs to the end of the log when its `end` never comes.
    """

    before: int = 0
    after: int = 0
    from_: Node | None = None
    from_within: int = 0
    until: Node | None = None
    while_: Node | None = None
    max_lines: int | None = None


@dataclass(frozen=True)
class VariantPlan:
    """One spelling of an output: how to find its anchors, and what to keep around them.

    Attributes:
        name: The variant's key; "" for an output with a plain `match`.
        match: The anchor condition.
        window: What to keep around each anchor.
    """

    name: str
    match: Node
    window: WindowPlan


@dataclass(frozen=True)
class OutputPlan:
    """One output and its spellings."""

    output: Output
    variants: tuple[VariantPlan, ...]


@dataclass(frozen=True)
class Plan:
    """Everything `run` needs: every regex once, and every output's tree over them.

    Attributes:
        leaves: Every expanded RE2 pattern, deduplicated. A `Leaf` is an index here.
        outputs: In load order.
    """

    leaves: tuple[str, ...] = ()
    outputs: tuple[OutputPlan, ...] = ()


def to_json(value: Any) -> Any:  # noqa: ANN401 - walks any Plan node
    """A Plan as plain JSON data, every node tagged with its type.

    Args:
        value: A Plan or any part of it.

    Returns:
        Dicts, lists, strings, numbers and None.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {"type": type(value).__name__} | {
            f.name: to_json(getattr(value, f.name)) for f in fields(value)
        }
    if isinstance(value, tuple | list):
        return [to_json(v) for v in value]
    return value
