"""A `match`: a tree of nodes, each a one-key mapping (`regex:`, `all:`, `preceded_by:` ...).

The key picks the node type, so an error names the node the author meant instead of
listing why the value fits none of eight types.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Discriminator, Field, StringConstraints, model_validator
from pydantic import Tag as Kind

from ci_doctor.engine.spec.base import Strict
from ci_doctor.engine.spec.limits import MAX_REACH

#: The keys that name a node.
NODE_KEYS = ("regex", "template", "all", "any", "not", "inside", "preceded_by", "followed_by")

Pattern = Annotated[str, StringConstraints(min_length=1)]


class RegexNode(Strict):
    """An RE2 pattern anywhere in the line. `%{NAME}` inserts a `define`."""

    regex: Pattern = Field(description="RE2, matched anywhere in the line. `%{NAME}` inserts a `define`.")


class TemplateNode(Strict):
    """The line as it looks in the log: `$NAME` a define, `...` anything, `$$` a `$`, the rest literal."""

    template: Pattern = Field(
        description="The line as it looks in the log: `$NAME` inserts a `define`, `...` is anything, "
        "`$$` is a `$`, and everything else is literal text. Matched anywhere in the line."
    )


class AllNode(Strict):
    """The line matches every item."""

    all: list["Node"] = Field(min_length=1, description="The line matches every item.")


class AnyNode(Strict):
    """The line matches at least one item."""

    any: list["Node"] = Field(min_length=1, description="The line matches at least one item.")


class NotNode(Strict):
    """The line doesn't match the item."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    not_: "Node" = Field(alias="not", description="The line doesn't match this.")


class Near(Strict):
    """A line-level match plus how far to look: `{regex: ..., within: N}` as written."""

    node: "Node"
    within: int = Field(ge=1, le=MAX_REACH, description=f"How many lines to look, 1-{MAX_REACH}.")

    @model_validator(mode="before")
    @classmethod
    def _inline(cls, data: Any) -> Any:  # noqa: ANN401 - raw YAML
        """Lift the node keys written beside `within` into `node`.

        Args:
            data: `{regex: ..., within: 3}` as written.

        Returns:
            `{node: {regex: ...}, within: 3}`.
        """
        if isinstance(data, dict) and "node" not in data:
            return {"within": data.get("within"), "node": {k: v for k, v in data.items() if k != "within"}}
        return data


class Block(Strict):
    """A block: from a `start` line to the next `end` line, both included."""

    start: "Node" = Field(description="Opens a block.")
    end: "Node" = Field(description="Closes it, on the first match after `start`.")
    max_lines: int = Field(
        ge=1, le=MAX_REACH, description="Closes it after this many lines if `end` never comes."
    )


class InsideNode(Strict):
    """The line sits inside such a block."""

    inside: Block


class PrecededByNode(Strict):
    """A matching line is among the `within` lines above."""

    preceded_by: Near


class FollowedByNode(Strict):
    """A matching line is among the `within` lines below."""

    followed_by: Near


def _kind(value: Any) -> str | None:  # noqa: ANN401 - raw YAML or a model
    """Which node a value is: its one node key.

    Args:
        value: A mapping as written, or an already-built node.

    Returns:
        The key, or None when it has none or several (pydantic then reports it).
    """
    if isinstance(value, BaseModel):
        keys = [k for k in NODE_KEYS if getattr(value, "not_" if k == "not" else k, None) is not None]
    elif isinstance(value, dict):
        keys = [k for k in value if k in NODE_KEYS]
    else:
        return None
    return keys[0] if len(keys) == 1 else None


#: One node of a `match` tree.
Node = Annotated[
    Annotated[RegexNode, Kind("regex")]
    | Annotated[TemplateNode, Kind("template")]
    | Annotated[AllNode, Kind("all")]
    | Annotated[AnyNode, Kind("any")]
    | Annotated[NotNode, Kind("not")]
    | Annotated[InsideNode, Kind("inside")]
    | Annotated[PrecededByNode, Kind("preceded_by")]
    | Annotated[FollowedByNode, Kind("followed_by")],
    Discriminator(
        _kind,
        custom_error_type="node",
        custom_error_message="a match node has exactly one of " + ", ".join(NODE_KEYS),
    ),
]

for _model in (AllNode, AnyNode, NotNode, Near, Block):
    _model.model_rebuild()
