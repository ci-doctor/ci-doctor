"""Checks on a compiled match: what it costs, and that it can't fire on every line."""

from ci_doctor.engine.errors import CompileError
from ci_doctor.engine.plan import AllOf, AnyOf, FollowedBy, Inside, Leaf, Node, Not, PrecededBy
from ci_doctor.engine.spec.limits import MAX_PATTERN_CHARS, MAX_PROGRAM_SIZE
from ci_doctor.lib.regex import compile_user, programsize


def check_regex(regex: str, path: str) -> str:
    """Hold one expanded regex to what it may cost on every log line.

    Args:
        regex: After `define` expansion.
        path: Where it came from, for errors.

    Returns:
        The regex, unchanged.

    Raises:
        CompileError: If RE2 rejects it, or it is too long or too large a program.
    """
    try:
        size = programsize(regex)
    except ValueError as err:
        raise CompileError(path, str(err)) from None
    if len(regex) > MAX_PATTERN_CHARS:
        raise CompileError(path, f"expands to {len(regex)} characters, over {MAX_PATTERN_CHARS}")
    if size > MAX_PROGRAM_SIZE:
        raise CompileError(path, f"compiles to {size} RE2 instructions, over {MAX_PROGRAM_SIZE}")
    return regex


def matches_empty_line(node: Node, leaves: tuple[str, ...]) -> bool:
    """Whether `node` holds on a log that is one empty line, so it would fire everywhere.

    A relation needs another line, so it never holds there; a block opens on its
    own first line, so `inside` holds when its `start` does.

    Args:
        node: A compiled match.
        leaves: The regexes its leaves index.

    Returns:
        True when the match fires on an empty line.
    """
    match node:
        case Leaf(index):
            return compile_user(leaves[index]).search("") is not None
        case AllOf(items):
            return all(matches_empty_line(i, leaves) for i in items)
        case AnyOf(items):
            return any(matches_empty_line(i, leaves) for i in items)
        case Not(item):
            return not matches_empty_line(item, leaves)
        case Inside(start, _, _):
            return matches_empty_line(start, leaves)
        case PrecededBy() | FollowedBy():
            return False
    raise TypeError(f"not a match node: {node!r}")
