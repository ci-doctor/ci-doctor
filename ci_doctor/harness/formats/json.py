"""JSON config files: set or remove one key, add or drop allow-list entries."""

import copy
import json
from pathlib import Path
from typing import Any

from ci_doctor.harness.formats import Change, Unsafe, read, write

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
    _parent(path, data, keys)[keys[-1]] = value
    return _save(path, before, original, data)


def unset_json(path: Path, keys: Keys) -> Change:
    """Remove one key, and the parents that leaves empty. The file itself stays.

    Args:
        path: The JSON file.
        keys: The key path.

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
    _prune(parents, keys)
    return _save(path, before, original, data, removing=True)


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
    current = _parent(path, data, keys).setdefault(keys[-1], [])
    if not isinstance(current, list):
        raise Unsafe(f"{path.name}: `{keys[-1]}` is not a list")
    current.extend(item for item in items if item not in current)
    return _save(path, before, original, data)


def drop_json_items(path: Path, keys: Keys, prefix: str) -> Change:
    """Remove the strings starting with `prefix` from a JSON list, and the list and parents left empty.

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
    _prune([*parents, kept], keys)
    return _save(path, before, original, data, removing=True)


def _dump(data: dict) -> str:
    """JSON as agents write it: 2-space indent and a final newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _load(path: Path) -> tuple[str | None, dict]:
    """A JSON object file's text and data; `(None, {})` when it does not exist."""
    before = read(path)
    if before is None or not before.strip():
        return before, {}
    try:
        data = json.loads(before)
    except json.JSONDecodeError as err:
        # JSONC (comments, trailing commas) lands here too: a rewrite would lose its comments.
        raise Unsafe(
            f"{path.name} is not plain JSON ({err.msg}, line {err.lineno}); "
            "files with comments are not edited"
        ) from None
    if not isinstance(data, dict):
        raise Unsafe(f"{path.name} is not a JSON object")
    return before, data


def _parent(path: Path, data: dict, keys: Keys) -> dict:
    """The object holding `keys[-1]`, created along the way if missing."""
    node = data
    for key in keys[:-1]:
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise Unsafe(f"{path.name}: `{key}` is not an object")
    return node


def _parents(data: dict, keys: Keys) -> list[dict] | None:
    """The objects along `keys[:-1]`, from the root; None if one is missing."""
    parents = [data]
    for key in keys[:-1]:
        child = parents[-1].get(key)
        if not isinstance(child, dict):
            return None
        parents.append(child)
    return parents


def _prune(nodes: list[Any], keys: Keys) -> None:
    """Drop the containers along `keys` that are now empty, deepest first; the root stays.

    Args:
        nodes: The root, then the container at each key of `keys` that exists.
        keys: The key path the nodes sit at.
    """
    for depth in range(len(nodes) - 1, 0, -1):
        if nodes[depth] not in ({}, []):
            break
        del nodes[depth - 1][keys[depth - 1]]


def _save(path: Path, before: str | None, original: dict, data: dict, *, removing: bool = False) -> Change:
    """Write `data` unless it equals `original`. Never empties the file: `{}` stays `{}`."""
    if before is not None and data == original:
        return Change(path, "absent" if removing else "unchanged")
    return write(path, before, _dump(data), removing=removing)
