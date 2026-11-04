"""Edit the `matchers:` block of a config file and leave the rest of it alone.

Round-tripping the whole file through a YAML dumper would drop every comment, and a
round-trip-preserving library is a new dependency for one block. So the block is spliced
as text: only `matchers:` is re-dumped; everything else survives byte for byte. The cost,
documented, is that comments *inside* the block are lost. The result is re-parsed and
checked before anything is written, so a mis-splice fails loudly instead of corrupting
the user's config.
"""

import copy
import re
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError

from ci_doctor.config.schema import Config
from ci_doctor.errors import SetupError
from ci_doctor.matchers.validate import atomic_write

_KEY_LINE = re.compile(r"^matchers\s*:")
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


def read_block(path: Path) -> dict:
    """The file's own `matchers:` mapping (not the merged defaults).

    Args:
        path: The config file, which may not exist.

    Returns:
        A copy of the block, `{}` when absent.
    """
    return copy.deepcopy(_read(path)[1].get("matchers") or {})


def _splice(text: str, dumped: str) -> str:
    """Replace the `matchers:` block in `text` with `dumped`, or append it.

    Args:
        text: The file's current text.
        dumped: The new block, ending in a newline.

    Returns:
        The new file text.
    """
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if _KEY_LINE.match(ln)), None)
    if start is None:
        return text + ("" if not text or text.endswith("\n") else "\n") + dumped
    end = next((i for i in range(start + 1, len(lines)) if _TOP_LEVEL.match(lines[i])), len(lines))
    while end > start + 1 and lines[end - 1].startswith("#"):  # comments above the next key are its own
        end -= 1
    tail = end
    while tail > start + 1 and not lines[tail - 1].strip():  # keep the blank gap before what follows
        tail -= 1
    return "".join(lines[:start]) + dumped + "".join(lines[tail:])


def edit_matchers(path: Path, mutate: Callable[[dict], None]) -> None:
    """Apply `mutate` to the file's `matchers` mapping and write it back safely.

    Args:
        path: The config file; created if missing.
        mutate: Changes the mapping in place.

    Raises:
        SetupError: If the file is unreadable, or the edit would change anything but
            `matchers` or produce an invalid config. The file is then untouched.
    """
    text, data = _read(path)
    block = copy.deepcopy(data.get("matchers") or {})
    mutate(block)
    dumped = yaml.safe_dump({"matchers": block}, sort_keys=False, default_flow_style=False)
    new_text = _splice(text, dumped)

    expected = {**data, "matchers": block}
    try:
        reparsed = yaml.safe_load(new_text)
        if reparsed != expected:
            raise SetupError(f"{path}: refusing to edit, the result would change more than `matchers`")
        Config.model_validate(reparsed)
    except (yaml.YAMLError, ValidationError) as err:
        raise SetupError(f"{path}: refusing to write an invalid config: {err}") from None

    atomic_write(path, new_text)
