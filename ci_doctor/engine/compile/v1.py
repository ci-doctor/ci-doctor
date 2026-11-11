"""The schema 1 front end: a `schema: 1` matcher → its outputs in the Plan."""

import functools
from collections.abc import Collection, Mapping

from ci_doctor.engine.compile.checks import check_regex, matches_empty_line
from ci_doctor.engine.compile.defines import expand_regex, expand_template
from ci_doctor.engine.compile.leaves import Leaves
from ci_doctor.engine.errors import CompileError
from ci_doctor.engine.plan import (
    AllOf,
    AnyOf,
    FollowedBy,
    Inside,
    Node,
    Not,
    Output,
    OutputPlan,
    PrecededBy,
    VariantPlan,
    WindowPlan,
)
from ci_doctor.engine.spec.limits import MAX_DEPTH, MAX_NODES
from ci_doctor.engine.spec.v1.match import (
    AllNode,
    AnyNode,
    FollowedByNode,
    InsideNode,
    NotNode,
    PrecededByNode,
    RegexNode,
    TemplateNode,
)
from ci_doctor.engine.spec.v1.match import Node as NodeSpec
from ci_doctor.engine.spec.v1.matcher import Matcher
from ci_doctor.engine.spec.v1.window import Window

_SEP = " › "


class _Tree:
    """Compiles one `match` tree, counting its nodes against the caps."""

    def __init__(self, defines: Mapping[str, str], leaves: Leaves, root: str) -> None:
        self.defines, self.leaves, self.root = defines, leaves, root
        self.nodes = 0

    def node(self, spec: NodeSpec, path: str, depth: int = 1, *, line_only: bool = False) -> Node:
        """Compile one node and its children.

        Args:
            spec: The node as validated.
            path: Its place in the file, e.g. `match.all[1]`.
            depth: How deep it sits; the root is 1.
            line_only: Inside a relation or a window part, where only `regex`,
                `template`, `all`, `any` and `not` keep evaluation linear.

        Returns:
            The Plan node.

        Raises:
            CompileError: Naming the path.
        """
        where = f"{self.root}{_SEP}{path}"
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise CompileError(where, f"the match has more than {MAX_NODES} nodes")
        if depth > MAX_DEPTH:
            raise CompileError(where, f"nests deeper than {MAX_DEPTH}")
        if isinstance(spec, RegexNode | TemplateNode):
            return self._leaf(spec, where)
        if isinstance(spec, AllNode | AnyNode | NotNode):
            return self._logic(spec, path, depth, line_only)
        if line_only:
            raise CompileError(where, "only regex, template, all, any and not are allowed here")
        return self._relation(spec, path, depth)

    def _leaf(self, spec: RegexNode | TemplateNode, where: str) -> Node:
        """A regex or template, expanded and checked, as a leaf of the Plan."""
        if isinstance(spec, RegexNode):
            regex = expand_regex(spec.regex, self.defines, where)
        else:
            regex = expand_template(spec.template, self.defines, where)
        return self.leaves(check_regex(regex, where))

    def _logic(self, spec: AllNode | AnyNode | NotNode, path: str, depth: int, line_only: bool) -> Node:
        """`all`, `any` or `not`: combinations of what one line matches."""
        match spec:
            case AllNode(all=items):
                return AllOf(self._each(items, f"{path}.all", depth, line_only))
            case AnyNode(any=items):
                return AnyOf(self._each(items, f"{path}.any", depth, line_only))
            case NotNode(not_=item):
                return Not(self.node(item, f"{path}.not", depth + 1, line_only=line_only))

    def _relation(self, spec: NodeSpec, path: str, depth: int) -> Node:
        """`inside`, `preceded_by` or `followed_by`: a line related to the lines around it."""
        line = functools.partial(self.node, depth=depth + 1, line_only=True)
        match spec:
            case InsideNode(inside=block):
                start, end = line(block.start, f"{path}.inside.start"), line(block.end, f"{path}.inside.end")
                return Inside(start, end, block.max_lines)
            case PrecededByNode(preceded_by=near):
                return PrecededBy(line(near.node, f"{path}.preceded_by"), near.within)
            case FollowedByNode(followed_by=near):
                return FollowedBy(line(near.node, f"{path}.followed_by"), near.within)
        raise CompileError(f"{self.root}{_SEP}{path}", f"not a match node: {spec!r}")

    def _each(self, items: list[NodeSpec], path: str, depth: int, line_only: bool) -> tuple[Node, ...]:
        return tuple(
            self.node(n, f"{path}[{i}]", depth + 1, line_only=line_only) for i, n in enumerate(items)
        )


def _match(spec: NodeSpec, defines: Mapping[str, str], leaves: Leaves, root: str) -> Node:
    node = _Tree(defines, leaves, root).node(spec, "match")
    if matches_empty_line(node, leaves.table()):
        raise CompileError(f"{root}{_SEP}match", "matches an empty line, so it fires everywhere")
    return node


def _window(spec: Window | None, defines: Mapping[str, str], leaves: Leaves, root: str) -> WindowPlan:
    if spec is None:
        return WindowPlan()

    def part(node: NodeSpec | None, name: str) -> Node | None:
        return (
            None
            if node is None
            else _Tree(defines, leaves, root).node(node, f"window.{name}", line_only=True)
        )

    return WindowPlan(
        before=spec.before,
        after=spec.after,
        from_=part(spec.from_.node if spec.from_ else None, "from"),
        from_within=spec.from_.within if spec.from_ else 0,
        until=part(spec.until, "until"),
        while_=part(spec.while_, "while"),
        max_lines=spec.max_lines,
    )


def compile_v1(m: Matcher, leaves: Leaves, exclude: Collection[str] = ()) -> list[OutputPlan]:
    """Compile a schema 1 matcher into its outputs.

    Args:
        m: The validated matcher.
        leaves: The Plan's leaf table, shared across matchers.
        exclude: Output ids to skip, so their regexes never reach the leaf table.

    Returns:
        One OutputPlan per output kept, in file order.

    Raises:
        CompileError: With a path like `ruff › outputs.check › variants.full › match.all[1]`.
    """
    out = []
    for output_id, where, o in m.named_outputs():
        if output_id in exclude:
            continue
        root = _SEP.join(p for p in (m.id, where) if p)
        if o.match is not None:
            variants = [
                VariantPlan(
                    "", _match(o.match, m.define, leaves, root), _window(o.window, m.define, leaves, root)
                )
            ]
        else:
            variants = []
            for name, v in (o.variants or {}).items():
                vroot = f"{root}{_SEP}variants.{name}"
                window = _window(v.window or o.window, m.define, leaves, vroot)
                variants.append(VariantPlan(name, _match(v.match, m.define, leaves, vroot), window))
        out.append(OutputPlan(Output(output_id, o.role, o.description), tuple(variants)))
    return out
