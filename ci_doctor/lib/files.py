"""Writing a user's file without ever half-writing it."""

import os
import stat
import tempfile
from pathlib import Path

from ci_doctor.errors import SetupError


def atomic_write(path: Path, text: str) -> None:
    """Replace a file's contents with text, in one rename (see :func:`atomic_write_bytes`).

    Args:
        path: The file; created if missing.
        text: Its new contents, written as UTF-8 with no newline translation.

    Raises:
        SetupError: If the file can't be written, e.g. its directory is missing.
    """
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: Path, body: bytes) -> None:
    """Replace a file's contents in one rename, keeping it the same file.

    A concurrent reader sees the old file or the new one, never half of either.
    Writes through a symlink and keeps the mode; a new file gets `0o666 & ~umask`.

    Args:
        path: The file; created if missing.
        body: Its new contents, byte for byte.

    Raises:
        SetupError: If the file can't be written, e.g. its directory is missing.
    """
    real = path.resolve()
    try:
        mode = stat.S_IMODE(real.stat().st_mode)
    except FileNotFoundError:
        umask = os.umask(0o077)  # the only way to read it is to set it; probe with a safe value
        os.umask(umask)
        mode = 0o666 & ~umask
    try:
        fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.")
    except OSError as err:
        raise SetupError(f"{path}: cannot write: {err.strerror or err}") from None
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException as err:
        Path(tmp).unlink(missing_ok=True)
        if isinstance(err, OSError):
            raise SetupError(f"{path}: cannot write: {err.strerror or err}") from None
        raise
