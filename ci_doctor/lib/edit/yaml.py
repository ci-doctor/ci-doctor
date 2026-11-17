"""YAML config files: one entry in a block mapping, edited as text so comments stay.

The text edits understand plain block style: `key:` on its own line, children indented
under it. Every edit is then checked: the new text must parse to exactly the old data
with the intended change. Anything else, such as a flow-style `{...}` mapping, leaves
the file alone.
"""

import copy
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ci_doctor.lib.edit import Change, Unsafe, read, write

Lines = list[str]


def edit_yaml(
    path: Path,
    edit_text: Callable[[Lines], Lines],
    edit_data: Callable[[dict], None],
    *,
    removing: bool = False,
) -> Change:
    """Edit a YAML file's text, checked against the same edit on its data.

    Args:
        path: The YAML file; created if missing, unless removing.
        edit_text: Changes the lines.
        edit_data: Makes the same change to the parsed data.
        removing: Whether this edit removes ci-doctor's part.

    Returns:
        What changed.

    Raises:
        Unsafe: If the file does not parse, or the text edit does not match the data edit.
    """
    before = read(path)
    if removing and before is None:
        return Change(path, "absent")
    text = before or ""
    expected = copy.deepcopy(original := _load(path, text))
    edit_data(expected)
    try:
        lines: Lines = [*text.splitlines()]
        lines = edit_text(lines)
    except Unsafe as err:
        raise Unsafe(f"{path.name}: {err}") from None
    new = "\n".join(lines).rstrip("\n") + "\n" if any(line.strip() for line in lines) else ""
    if _load(path, new) != expected:
        raise Unsafe(f"{path.name}: its layout is not one ci-doctor can edit without changing something else")
    if expected == original:
        return Change(path, "absent" if removing else "unchanged")
    return write(path, before, new, removing=removing)


def set_key(lines: Lines, key: str, value: object) -> Lines:
    """Set top-level `key` to `value`, replacing its whole block (flow style too) or appending it.

    Args:
        lines: The file's lines.
        key: A top-level key.
        value: Its new value.

    Returns:
        The new lines.
    """
    rendered = yaml.safe_dump({key: value}, sort_keys=False, default_flow_style=False).splitlines()
    found = _block(lines, key, "", inline_ok=True)
    if found is None:
        return [*lines, *rendered]
    return [*lines[: found[0]], *rendered, *lines[found[1] :]]


def set_child(lines: Lines, parent: str, child: str, body: dict[str, Any]) -> Lines:
    """Set `parent.child` to `body`, adding `parent:` at the end if it is missing.

    Args:
        lines: The file's lines.
        parent: A top-level key, e.g. `mcp_servers`.
        child: The key under it, e.g. `ci-doctor`.
        body: The child's mapping.

    Returns:
        The new lines.
    """
    found = _block(lines, parent, "")
    if found is None:
        gap = [""] if any(line.strip() for line in lines) else []
        return [*lines, *gap, f"{parent}:", *_render(child, body, "  ")]
    start, end = found
    indent = _child_indent(lines, start, end)
    rendered = _render(child, body, indent)
    if (old := _block(lines, child, indent, start + 1, end)) is not None:
        return [*lines[: old[0]], *rendered, *lines[old[1] :]]
    return [*lines[:end], *rendered, *lines[end:]]


def remove_child(lines: Lines, parent: str, child: str) -> Lines:
    """Remove `parent.child`, and `parent:` itself if nothing is left under it.

    Args:
        lines: The file's lines.
        parent: A top-level key.
        child: The key under it.

    Returns:
        The new lines.
    """
    found = _block(lines, parent, "")
    if found is None:
        return lines
    start, end = found
    old = _block(lines, child, _child_indent(lines, start, end), start + 1, end)
    if old is None:
        return lines
    lines = [*lines[: old[0]], *lines[old[1] :]]
    end -= old[1] - old[0]
    if all(_blank_or_comment(line) for line in lines[start + 1 : end]):
        del lines[start]
    return lines


def add_item(lines: Lines, parent: str, child: str, item: str) -> Lines:
    """Append `- item` to the block list at `parent.child`, if that list exists.

    Args:
        lines: The file's lines.
        parent: A top-level key, e.g. `platform_toolsets`.
        child: The key holding the list, e.g. `cli`.
        item: The plain string to add.

    Returns:
        The new lines; unchanged when there is no such list or the item is already there.
    """
    items = _items(lines, parent, child)
    if not items or any(lines[i].strip() == f"- {item}" for i in items):
        return lines
    indent = lines[items[0]][: len(lines[items[0]]) - len(lines[items[0]].lstrip())]
    return [*lines[: items[-1] + 1], f"{indent}- {item}", *lines[items[-1] + 1 :]]


def remove_item(lines: Lines, parent: str, child: str, item: str) -> Lines:
    """Remove `- item` from the block list at `parent.child`.

    Args:
        lines: The file's lines.
        parent: A top-level key.
        child: The key holding the list.
        item: The string to remove.

    Returns:
        The new lines.
    """
    drop = {i for i in _items(lines, parent, child) if lines[i].strip() == f"- {item}"}
    return [line for i, line in enumerate(lines) if i not in drop]


def _items(lines: Lines, parent: str, child: str) -> list[int]:
    """The line numbers of the `- ...` items of the block list at `parent.child`."""
    found = _block(lines, parent, "")
    if found is None:
        return []
    start, end = found
    inner = _block(lines, child, _child_indent(lines, start, end), start + 1, end)
    if inner is None:
        return []
    return [i for i in range(inner[0] + 1, inner[1]) if lines[i].lstrip().startswith("- ")]


def _block(
    lines: Lines, key: str, indent: str, start: int = 0, stop: int | None = None, *, inline_ok: bool = False
) -> tuple[int, int] | None:
    """The lines `[first, end)` of `key:` written at exactly `indent`, with its children.

    Raises:
        Unsafe: If the key is written inline (`key: {...}`), which the text edits cannot
            extend; `inline_ok` allows it for a caller that replaces the whole block.
    """
    stop = len(lines) if stop is None else stop
    head = re.compile(rf"^{re.escape(indent)}{re.escape(key)}:")
    for first in range(start, stop):
        if head.match(lines[first]):
            if not inline_ok and lines[first][len(indent) + len(key) + 1 :].split("#")[0].strip():
                raise Unsafe(f"`{key}` is written inline")
            end = first + 1
            while end < stop and _inside(lines[end], len(indent)):
                end += 1
            while end > first + 1 and _blank_or_comment(lines[end - 1]):
                end -= 1  # a comment before the next key belongs to that key
            return first, end
    return None


def _child_indent(lines: Lines, start: int, end: int) -> str:
    """The indent the children of the block at `start` use; two spaces when it has none."""
    child = next((line for line in lines[start + 1 : end] if not _blank_or_comment(line)), None)
    return " " * _depth(child) if child else "  "


def _render(key: str, body: dict[str, Any], indent: str) -> Lines:
    """`key:` and its mapping as block-style lines at `indent`."""
    dumped = yaml.safe_dump(body, sort_keys=False, default_flow_style=False).splitlines()
    return [f"{indent}{key}:", *(f"{indent}{indent}{line}" for line in dumped)]


def _inside(line: str, depth: int) -> bool:
    """Whether a line below a key at `depth` still belongs to it.

    Deeper lines do, and so do `- item` lines at the same depth: YAML lets a list sit
    level with its key, and PyYAML writes it that way.
    """
    if _blank_or_comment(line) or _depth(line) > depth:
        return True
    return _depth(line) == depth and line.lstrip().startswith("- ")


def _depth(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank_or_comment(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def _load(path: Path, text: str) -> dict:
    """Parse a YAML mapping."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as err:
        raise Unsafe(f"{path.name} is not valid YAML ({err})") from None
    if not isinstance(data, dict):
        raise Unsafe(f"{path.name} is not a YAML mapping")
    return data
