"""Edits to another program's config files that change only ci-doctor's part.

One module per format. Each edit changes one entry and keeps the rest of the file. A
file that cannot be edited safely (it does not parse, or a rewrite would lose its
comments) raises `Unsafe`; `safely` turns that into a skipped change, and the file is
left alone.

Install records the files and the keys it created. On removal, only those may be
deleted once empty; everything the user had stays, an empty `mcpServers` included.
Anything missing from the record counts as the user's, so a lost record never deletes
anything.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ci_doctor.config.loader import user_config
from ci_doctor.lib.files import atomic_write

Action = Literal["created", "updated", "unchanged", "removed", "absent", "skipped"]


@dataclass(frozen=True)
class Change:
    """What one edit did to one file.

    Attributes:
        path: The file.
        action: What happened to it.
        note: Why it was skipped, when it was.
    """

    path: Path
    action: Action
    note: str = ""


class Unsafe(Exception):
    """A file ci-doctor will not edit; the message says why."""


def safely(path: Path, edit: Callable[[], Change]) -> Change:
    """Run one edit; an `Unsafe` file becomes a skipped change instead of an error.

    Args:
        path: The file the edit touches.
        edit: The edit.

    Returns:
        What the edit did, or a skipped change carrying the reason.
    """
    try:
        return edit()
    except Unsafe as err:
        return Change(path, "skipped", str(err))


def write(path: Path, before: str | None, text: str, *, removing: bool = False) -> Change:
    """Write `text` if it differs from `before`, keeping the record of what install created.

    Args:
        path: The file.
        before: Its current text, or None if it does not exist.
        text: Its new text. After a removal, empty text deletes a file install created.
        removing: Whether this edit removes ci-doctor's part.

    Returns:
        What changed.
    """
    if text == before:
        return Change(path, "absent" if removing else "unchanged")
    if removing:
        if not text.strip() and created(path):
            path.unlink()
        else:
            atomic_write(path, text)
        forget(path)
        return Change(path, "removed")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text)
    if before is not None:
        return Change(path, "updated")
    remember(path, file=True)
    return Change(path, "created")


def created(path: Path) -> bool:
    """Whether install created this file, so a removal may delete it."""
    return bool(_load().get(_key(path), {}).get("file"))


def created_keys(path: Path) -> set[tuple[str, ...]]:
    """The keys install added to this file, so a removal may drop them once empty."""
    return {tuple(k) for k in _load().get(_key(path), {}).get("keys", [])}


def remember(path: Path, *, file: bool = False, keys: list[tuple[str, ...]] | None = None) -> None:
    """Note that install created this file, or these keys in it."""
    record = _load()
    entry = record.setdefault(_key(path), {"file": False, "keys": []})
    entry["file"] = entry["file"] or file
    entry["keys"] = sorted({tuple(k) for k in entry["keys"]} | set(keys or []))
    _save(record)


def forget(path: Path) -> None:
    """Drop the record for a file once ci-doctor's part is removed from it."""
    record = _load()
    if record.pop(_key(path), None) is not None:
        _save(record)


def _key(path: Path) -> str:
    return str(path.resolve())


def _record_file() -> Path:
    return user_config().parent / "mcp-install-created.json"


def _load() -> dict[str, dict]:
    try:
        record = json.loads(_record_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def _save(record: dict[str, dict]) -> None:
    path = _record_file()
    if record:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    else:
        path.unlink(missing_ok=True)


def read(path: Path) -> str | None:
    """A file's text, or None if it does not exist."""
    return path.read_text(encoding="utf-8") if path.exists() else None
