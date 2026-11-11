"""Edit one top-level block of a config file and leave the rest byte for byte.

The block is spliced as text so the file's comments survive (only those inside the
block are lost); the result is re-parsed and validated before anything is written.
"""

import copy
import re
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError

from ci_doctor.config.schemas import Config
from ci_doctor.errors import SetupError
from ci_doctor.lib.files import atomic_write

_TOP_LEVEL = re.compile(r"^[^\s#]")


def _read(path: Path) -> tuple[str, dict]:
    """The file's text and parsed mapping; an absent file is empty.

    Args:
        path: The config file.

    Returns:
        The text and its top-level mapping.

    Raises:
        SetupError: If the file is unreadable or not a YAML mapping.
    """
    if not path.exists():
        return "", {}
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as err:
        raise SetupError(f"{path}: cannot read config: {err}") from None
    if not isinstance(data, dict):
        raise SetupError(f"{path}: expected a YAML mapping at the top level")
    return text, data


def read_block(path: Path, key: str) -> dict:
    """A copy of the file's own `key:` block, `{}` when absent.

    Args:
        path: The config file, which may not exist.
        key: A top-level config key.

    Returns:
        A copy of the block, `{}` when absent.
    """
    return copy.deepcopy(_read(path)[1].get(key) or {})


def _splice(text: str, key: str, dumped: str) -> str:
    """Replace the `key:` block in `text` with `dumped`, or append it.

    Args:
        text: The file's current text.
        key: The top-level key.
        dumped: The new block, ending in a newline.

    Returns:
        The new file text.
    """
    lines = text.splitlines(keepends=True)
    key_line = re.compile(rf"^{re.escape(key)}\s*:")
    start = next((i for i, ln in enumerate(lines) if key_line.match(ln)), None)
    if start is None:
        return text + ("" if not text or text.endswith("\n") else "\n") + dumped
    end = next((i for i in range(start + 1, len(lines)) if _TOP_LEVEL.match(lines[i])), len(lines))
    while end > start + 1 and lines[end - 1].startswith("#"):  # comments above the next key are its own
        end -= 1
    tail = end
    while tail > start + 1 and not lines[tail - 1].strip():  # keep the blank gap before what follows
        tail -= 1
    return "".join(lines[:start]) + dumped + "".join(lines[tail:])


def edit_block(path: Path, key: str, mutate: Callable[[dict], None]) -> None:
    """Apply `mutate` to the file's `key:` block and write it back.

    Args:
        path: The config file; created if missing.
        key: The top-level key to edit.
        mutate: Changes the mapping in place.

    Raises:
        SetupError: If the file is unreadable, or the edit would change anything but
            `key` or produce an invalid config. The file is then untouched.
    """
    text, data = _read(path)
    block = copy.deepcopy(data.get(key) or {})
    mutate(block)
    new_text = _splice(text, key, yaml.safe_dump({key: block}, sort_keys=False, default_flow_style=False))
    try:
        reparsed = yaml.safe_load(new_text)
        if reparsed != {**data, key: block}:
            raise SetupError(f"{path}: refusing to edit, the result would change more than `{key}`")
        Config.model_validate(reparsed)
    except (yaml.YAMLError, ValidationError) as err:
        raise SetupError(f"{path}: refusing to write an invalid config: {err}") from None
    atomic_write(path, new_text)
