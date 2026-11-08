"""The config files a command reads (the global `-f`) and the one it writes (`--write-to`)."""

from collections.abc import Callable
from pathlib import Path

import click

from ci_doctor.config.edit import edit_block, read_block
from ci_doctor.config.loader import load_config, user_config
from ci_doctor.config.schemas import Config

FILES = "ci_doctor.config_files"
WRITE_TO = "ci_doctor.write_to"


def _meta() -> dict:
    return click.get_current_context().meta


def config_files() -> list[Path]:
    """The `-f` files, lowest precedence first, then `--write-to` when it exists."""
    files, write_to = _meta().get(FILES, []), _meta().get(WRITE_TO)
    if write_to is not None and write_to.is_file() and write_to not in files:
        return [*files, write_to]
    return files


def target() -> Path:
    """The file edits go to: `--write-to`, else the user config."""
    return _meta().get(WRITE_TO) or user_config()


def editable() -> Config:
    """The effective config, tolerating a `target()` that doesn't exist yet."""
    edited = target()
    return load_config([p for p in config_files() if p != edited or p.is_file()])


def read(key: str) -> dict:
    """The target file's own `key:` block, `{}` when absent."""
    return read_block(target(), key)


def edit(key: str, mutate: Callable[[dict], None]) -> None:
    """Change the target file's `key:` block in place.

    Creates the user config's directory, never a `--write-to` one: a typo should fail.
    """
    path = target()
    if path == user_config():
        path.parent.mkdir(parents=True, exist_ok=True)
    edit_block(path, key, mutate)
