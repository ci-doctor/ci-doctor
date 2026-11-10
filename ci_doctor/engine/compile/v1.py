"""The schema 1 front end: a `schema: 1` matcher → its outputs in the Plan."""

from collections.abc import Mapping

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
        match spec:
            case RegexNode(regex=rx):
                return self.leaves(check_regex(expand_regex(rx, self.defines, where), where))
            case TemplateNode(template=tpl):
                return self.leaves(check_regex(expand_template(tpl, self.defines, where), where))
            case AllNode(all=items):
                return AllOf(self._each(items, f"{path}.all", depth, line_only))
            case AnyNode(any=items):
                return AnyOf(self._each(items, f"{path}.any", depth, line_only))
            case NotNode(not_=item):
                return Not(self.node(item, f"{path}.not", depth + 1, line_only=line_only))
        if line_only:
            raise CompileError(where, "only regex, template, all, any and not are allowed here")
        match spec:
            case InsideNode(inside=block):
                start = self.node(block.start, f"{path}.inside.start", depth + 1, line_only=True)
                end = self.node(block.end, f"{path}.inside.end", depth + 1, line_only=True)
                return Inside(start, end, block.max_lines)
            case PrecededByNode(preceded_by=near):
                return PrecededBy(
                    self.node(near.node, f"{path}.preceded_by", depth + 1, line_only=True), near.within
                )
            case FollowedByNode(followed_by=near):
                return FollowedBy(
                    self.node(near.node, f"{path}.followed_by", depth + 1, line_only=True), near.within
                )
        raise CompileError(where, f"not a match node: {spec!r}")

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


def compile_v1(m: Matcher, leaves: Leaves) -> list[OutputPlan]:
    """Compile a schema 1 matcher into its outputs.

    Args:
        m: The validated matcher.
        leaves: The Plan's leaf table, shared across matchers.

    Returns:
        One OutputPlan per output, in file order.

    Raises:
        CompileError: With a path like `ruff › outputs.check › variants.full › match.all[1]`.
    """
    out = []
    for output_id, where, o in m.named_outputs():
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
