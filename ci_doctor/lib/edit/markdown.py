"""Markdown files: a marked ci-doctor block inside the user's text, or a file of its own."""

from pathlib import Path

from ci_doctor.lib.edit import Change, read, write

START = "<!-- CI_DOCTOR_START -->"
END = "<!-- CI_DOCTOR_END -->"


def marked(body: str) -> str:
    """`body` between ci-doctor's markers, so it can be found and removed later."""
    return f"{START}\n{body}\n{END}"


def set_block(path: Path, body: str) -> Change:
    """Add or refresh the marked block, after whatever the file already says.

    Args:
        path: The Markdown file (`CLAUDE.md`, `AGENTS.md`, ...); created if missing.
        body: What goes between the markers.

    Returns:
        What changed.
    """
    before = read(path)
    text = before or ""
    start, end = text.find(START), text.find(END)
    if start != -1 and end > start:
        new = text[:start] + marked(body) + text[end + len(END) :]
    else:
        new = text.rstrip() + ("\n\n" if text.strip() else "") + marked(body) + "\n"
    return write(path, before, new)


def unset_block(path: Path) -> Change:
    """Remove the marked block; delete the file if nothing else is in it.

    Args:
        path: The Markdown file.

    Returns:
        What changed.
    """
    before = read(path)
    if before is None:
        return Change(path, "absent")
    start, end = before.find(START), before.find(END)
    if start == -1 or end < start:
        return Change(path, "absent")
    rest = "\n\n".join(part for part in (before[:start].rstrip(), before[end + len(END) :].strip()) if part)
    return write(path, before, rest + "\n" if rest else "", removing=True)


def set_file(path: Path, body: str) -> Change:
    """Write a file that holds only the marked block, such as Kiro's steering file.

    Args:
        path: The file.
        body: What goes between the markers.

    Returns:
        What changed.
    """
    return write(path, read(path), marked(body) + "\n")
