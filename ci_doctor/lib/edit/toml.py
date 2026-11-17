"""TOML config files: one `[table]`, edited as text so the user's comments stay."""

import tomllib
from pathlib import Path

from ci_doctor.lib.edit import Change, Unsafe, read, write


def set_toml_table(path: Path, header: str, body: list[str]) -> Change:
    """Add or replace one `[header]` table, keeping every other line.

    Sub-tables such as `[header.env]` are kept: the user may have put a token there.

    Args:
        path: The TOML file; created if missing.
        header: The table name, e.g. `mcp_servers.ci-doctor`.
        body: The table's `key = value` lines.

    Returns:
        What changed.

    Raises:
        Unsafe: If the file is not valid TOML, before or after the edit.
    """
    before = read(path)
    lines = _parse(path, before or "").splitlines()
    table = [f"[{header}]", *body]
    start = next((i for i, line in enumerate(lines) if line.strip() == f"[{header}]"), None)
    if start is None:
        lines = [*lines, "", *table] if any(line.strip() for line in lines) else table
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[start:end] = table
    return write(path, before, _parse(path, "\n".join(lines) + "\n"))


def unset_toml_table(path: Path, header: str) -> Change:
    """Remove the `[header]` table and its sub-tables; delete the file if nothing else is left.

    Args:
        path: The TOML file.
        header: The table name.

    Returns:
        What changed.

    Raises:
        Unsafe: If the file is not valid TOML.
    """
    before = read(path)
    if before is None:
        return Change(path, "absent")
    kept: list[str] = []
    ours = False
    for line in _parse(path, before).splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            name = stripped.strip("[]").strip()
            ours = name == header or name.startswith(f"{header}.")
        if not ours:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return write(path, before, "\n".join(kept) + "\n" if kept else "", removing=True)


def _parse(path: Path, text: str) -> str:
    """Return `text` if it is valid TOML."""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise Unsafe(f"{path.name} is not valid TOML ({err})") from None
    return text
