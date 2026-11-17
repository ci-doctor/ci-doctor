"""Edits to a config file that change one entry and keep the rest of the file.

One module per format. A
file that cannot be edited safely (it does not parse, or a rewrite would lose its
comments) raises `Unsafe`; `safely` turns that into a skipped change, and the file is
left alone.

Removal takes out the entry and any container it leaves empty. A JSON config is never
deleted, since the program that owns it manages it; a Markdown, TOML or YAML file left
with nothing in it is.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    """Write `text` if it differs from `before`.

    Args:
        path: The file.
        before: Its current text, or None if it does not exist.
        text: Its new text. After a removal, empty text deletes the file: nothing is left
            in it, so there is nothing of the user's to keep.
        removing: Whether this edit removes ci-doctor's part.

    Returns:
        What changed.
    """
    if text == before:
        return Change(path, "absent" if removing else "unchanged")
    if removing:
        if text.strip():
            atomic_write(path, text)
        else:
            path.unlink()
        return Change(path, "removed")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, text)
    return Change(path, "updated" if before is not None else "created")


def read(path: Path) -> str | None:
    """A file's text, or None if it does not exist."""
    return path.read_text(encoding="utf-8") if path.exists() else None
