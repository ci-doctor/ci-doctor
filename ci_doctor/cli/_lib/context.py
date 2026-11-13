"""The config files a command reads (the global `-f`) and the one it writes (`--write-to`)."""

from collections.abc import Callable
from pathlib import Path

import click

from ci_doctor.config.files import ConfigFiles
from ci_doctor.config.schemas import Config

FILES = "ci_doctor.config_files"
WRITE_TO = "ci_doctor.write_to"


def files() -> ConfigFiles:
    """The global `-f` and `--write-to`, as the services take them.

    Returns:
        The files this invocation reads and the one it edits.
    """
    meta = click.get_current_context().meta
    return ConfigFiles(meta.get(FILES, []), meta.get(WRITE_TO))


def config_files() -> list[Path]:
    """The `-f` files, lowest precedence first, then `--write-to` when it exists.

    Returns:
        The files, empty when none were given.
    """
    return files().paths()


def target() -> Path:
    """The file edits go to: `--write-to`, else the user config.

    Returns:
        The path, which may not exist yet.
    """
    return files().target()


def editable() -> Config:
    """The effective config, tolerating a `target()` that doesn't exist yet.

    Returns:
        The effective config.

    Raises:
        SetupError: If it does not validate, or a file other than the target is missing.
    """
    return files().editable()


def read(key: str) -> dict:
    """The target file's own `key:` block, `{}` when absent.

    Args:
        key: A top-level config key.

    Returns:
        A copy of the block, `{}` when the file or the key is absent.
    """
    return files().read(key)


def edit(key: str, mutate: Callable[[dict], None]) -> None:
    """Change the target file's `key:` block in place.

    Args:
        key: A top-level config key.
        mutate: Changes the block in place.
    """
    files().edit(key, mutate)
