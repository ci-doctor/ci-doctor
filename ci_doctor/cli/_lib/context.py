"""The config files a command reads (the global `-f`) and the one it writes (`--write-to`)."""

from collections.abc import Callable
from pathlib import Path

import click

from ci_doctor.config.edit import edit_block, read_block
from ci_doctor.config.loader import load_config, user_config
from ci_doctor.config.schemas import Config

#: `ctx.meta` keys the root callback sets.
FILES = "ci_doctor.config_files"
WRITE_TO = "ci_doctor.write_to"


def _meta() -> dict:
    return click.get_current_context().meta


def config_files() -> list[Path]:
    """The `-f` files, lowest precedence first, then `--write-to` when it exists.

    Returns:
        The files, empty when none were given.
    """
    files, write_to = _meta().get(FILES, []), _meta().get(WRITE_TO)
    if write_to is not None and write_to.is_file() and write_to not in files:
        return [*files, write_to]
    return files


def target() -> Path:
    """The file a command that edits the config writes: `--write-to`, else the user config.

    Returns:
        The path, which may not exist yet.
    """
    return _meta().get(WRITE_TO) or user_config()


def editable() -> Config:
    """The config as `analyze` would see it, except that a missing `target()` adds nothing.

    For the commands that create the file they edit.

    Returns:
        The effective config.

    Raises:
        SetupError: If it does not validate, or a file other than the target is missing.
    """
    edited = target()
    return load_config([p for p in config_files() if p != edited or p.is_file()])


def read(key: str) -> dict:
    """The target file's own `key:` block, as `edit` would change it.

    Args:
        key: A top-level config key.

    Returns:
        A copy of the block, `{}` when the file or the key is absent.
    """
    return read_block(target(), key)


def edit(key: str, mutate: Callable[[dict], None]) -> None:
    """Change the target file's `key:` block; see :func:`~ci_doctor.config.edit.edit_block`.

    The user config's directory is created on first write; a `--write-to` path's is not,
    so a typo there fails instead of leaving directories behind.

    Args:
        key: A top-level config key.
        mutate: Changes the block in place.
    """
    path = target()
    if path == user_config():
        path.parent.mkdir(parents=True, exist_ok=True)
    edit_block(path, key, mutate)
