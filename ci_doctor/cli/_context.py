"""The global `ci-doctor -f` files, as every command reads them."""

from pathlib import Path

import click

from ci_doctor.config.loader import load_config
from ci_doctor.config.schemas import Config

_DEFAULT_CONFIG = Path(".ci-doctor.yml")


def config_files() -> list[Path]:
    """The `-f` files, lowest precedence first.

    Returns:
        The files, empty when none were given.
    """
    return click.get_current_context().obj or []


def target() -> Path:
    """The file a command that edits the config writes: the last `-f`, else `./.ci-doctor.yml`.

    Returns:
        The path, which may not exist yet.
    """
    files = config_files()
    return files[-1] if files else _DEFAULT_CONFIG


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
