"""Writing a user's file without ever half-writing it."""

import os
import stat
import tempfile
from pathlib import Path

from ci_doctor.errors import SetupError


def atomic_write(path: Path, text: str) -> None:
    """Replace a file's contents in one rename, keeping it the same file.

    Writes through a symlink and keeps the mode; a new file gets `0o666 & ~umask`.

    Raises:
        SetupError: If the file can't be written.
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
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException as err:
        Path(tmp).unlink(missing_ok=True)
        if isinstance(err, OSError):
            raise SetupError(f"{path}: cannot write: {err.strerror or err}") from None
        raise
