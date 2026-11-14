"""JSON config files: set or remove one key, add or drop allow-list entries."""

import copy
import json
from pathlib import Path
from typing import Any

from ci_doctor.harness.formats import Change, Unsafe, created, created_keys, read, remember, write

Keys = tuple[str, ...]


def set_json(path: Path, keys: Keys, value: Any, *, new: dict | None = None) -> Change:  # noqa: ANN401 - any JSON value
    """Set one key, creating its parent objects.

    Args:
        path: The JSON file; created if missing.
        keys: The key path, e.g. `("mcpServers", "ci-doctor")`.
        value: The value to set.
        new: What a file that does not exist yet starts with.

    Returns:
        What changed.

    Raises:
        Unsafe: If the file is not a JSON object, or a parent key is not an object.
    """
    before, data = _load(path)
    if before is None and new:
        data = copy.deepcopy(new)
    original = copy.deepcopy(data)
    parent, added = _parent(path, data, keys)
    parent[keys[-1]] = value
    return _save(path, before, original, data, added=added)


def unset_json(path: Path, keys: Keys, *, new: dict | None = None) -> Change:
    """Remove one key, and the parents install added if that leaves them empty.

    Args:
        path: The JSON file.
        keys: The key path.
        new: What `set_json` started a new file with; a file install created that holds
            only this afterwards is deleted.

    Returns:
        What changed; `absent` when the key was not there.

    Raises:
        Unsafe: If the file is not a JSON object.
    """
    before, data = _load(path)
    original = copy.deepcopy(data)
    parents = _parents(data, keys)
    if parents is None or keys[-1] not in parents[-1]:
        return Change(path, "absent")
    del parents[-1][keys[-1]]
    _prune(path, parents, keys)
    return _save(path, before, original, data, removing=True, empty=new or {})


def add_json_items(path: Path, keys: Keys, items: list[str]) -> Change:
    """Add strings to a JSON list, creating it and its parents; an item already there stays once.

    Args:
        path: The JSON file; created if missing.
        keys: The key path to the list.
        items: The strings to add.

    Returns:
        What changed.

    Raises:
        Unsafe: If the file is not a JSON object, or the key is not a list.
    """
    before, data = _load(path)
    original = copy.deepcopy(data)
    parent, added = _parent(path, data, keys)
    if keys[-1] not in parent:
        added.append(keys)
    current = parent.setdefault(keys[-1], [])
    if not isinstance(current, list):
        raise Unsafe(f"{path.name}: `{keys[-1]}` is not a list")
    current.extend(item for item in items if item not in current)
    return _save(path, before, original, data, added=added)


def drop_json_items(path: Path, keys: Keys, prefix: str) -> Change:
    """Remove the strings starting with `prefix` from a JSON list, and what install added if left empty.

    Args:
        path: The JSON file.
        keys: The key path to the list.
        prefix: Items starting with it are removed.

    Returns:
        What changed; `absent` when nothing matched.

    Raises:
        Unsafe: If the file is not a JSON object.
    """
    before, data = _load(path)
    original = copy.deepcopy(data)
    parents = _parents(data, keys)
    items = parents[-1].get(keys[-1]) if parents else None
    if parents is None or not isinstance(items, list):
        return Change(path, "absent")
    kept = [i for i in items if not (isinstance(i, str) and i.startswith(prefix))]
    if len(kept) == len(items):
        return Change(path, "absent")
    parents[-1][keys[-1]] = kept
    _prune(path, [*parents, kept], (*keys, ""))
    return _save(path, before, original, data, removing=True, empty={})


def has_comments(text: str) -> bool:
    """Whether JSON text has `//` or `/* */` comments outside its strings.

    Args:
        text: The file's text.

    Returns:
        True if it has a comment.
    """
    in_string = escaped = False
    for here, nxt in zip(text, text[1:] + " ", strict=True):
        if in_string:
            if escaped:
                escaped = False
            elif here == "\\":
                escaped = True
            elif here == '"':
                in_string = False
        elif here == '"':
            in_string = True
        elif here == "/" and nxt in "/*":
            return True
    return False


def _dump(data: dict, before: str | None) -> str:
    """JSON in the file's own style: one line or indented, and its final newline; new files get 2 spaces."""
    newline = "\n" if before is None or before.endswith("\n") else ""
    if before is not None and "\n" not in before.strip():
        separators = (", ", ": ") if '": ' in before else (",", ":")
        return json.dumps(data, separators=separators, ensure_ascii=False) + newline
    indent: int | str = 2
    for line in (before or "").splitlines()[1:]:
        if lead := line[: len(line) - len(line.lstrip())]:
            indent = "\t" if lead.startswith("\t") else len(lead)
            break
    return json.dumps(data, indent=indent, ensure_ascii=False) + newline


def _load(path: Path) -> tuple[str | None, dict]:
    """A JSON object file's text and data; `(None, {})` when it does not exist."""
    before = read(path)
    if before is None or not before.strip():
        return before, {}
    if has_comments(before):
        raise Unsafe(f"{path.name} has comments, which rewriting it would lose")
    try:
        data = json.loads(before)
    except json.JSONDecodeError as err:
        raise Unsafe(f"{path.name} is not valid JSON ({err.msg}, line {err.lineno})") from None
    if not isinstance(data, dict):
        raise Unsafe(f"{path.name} is not a JSON object")
    return before, data


def _parent(path: Path, data: dict, keys: Keys) -> tuple[dict, list[Keys]]:
    """The object holding `keys[-1]`, and the key paths created to reach it."""
    node, added = data, []
    for depth, key in enumerate(keys[:-1], start=1):
        if key not in node:
            added.append(keys[:depth])
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise Unsafe(f"{path.name}: `{key}` is not an object")
    return node, added


def _parents(data: dict, keys: Keys) -> list[dict] | None:
    """The objects along `keys[:-1]`, from the root; None if one is missing."""
    parents = [data]
    for key in keys[:-1]:
        child = parents[-1].get(key)
        if not isinstance(child, dict):
            return None
        parents.append(child)
    return parents


def _prune(path: Path, nodes: list[Any], keys: Keys) -> None:
    """Drop the containers along `keys` that are now empty, if install added them (or the file)."""
    ours = created_keys(path)
    whole_file = created(path)
    for depth in range(len(nodes) - 1, 0, -1):
        if nodes[depth] in ({}, []) and (whole_file or keys[:depth] in ours):
            del nodes[depth - 1][keys[depth - 1]]
        else:
            break


def _save(
    path: Path,
    before: str | None,
    original: dict,
    data: dict,
    *,
    added: list[Keys] | None = None,
    removing: bool = False,
    empty: dict | None = None,
) -> Change:
    """Write `data` unless it equals `original`, recording any keys install added."""
    if before is not None and data == original:
        return Change(path, "absent" if removing else "unchanged")
    emptied = removing and data == empty and created(path)  # only a file install made may go
    text = "" if emptied else _dump(data, before)
    change = write(path, before, text, removing=removing)
    if added and change.action == "updated":
        remember(path, keys=added)
    return change
