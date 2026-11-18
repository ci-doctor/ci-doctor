"""The config files a command reads (the global `-f`) and the one it writes (`--write-to`)."""

from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path

from ci_doctor.config.files import ConfigFiles
from ci_doctor.config.schemas import Config

# Ours, not click's context: typer 0.26+ runs on its own copy of click, whose context
# `click.get_current_context()` cannot see.
_FILES: ContextVar[ConfigFiles | None] = ContextVar("ci_doctor_config_files", default=None)


def use(paths: list[Path], write_to: Path | None) -> Callable[[], None]:
    """Record the global `-f` and `--write-to` for one invocation; the root callback calls it.

    Args:
        paths: The `-f` files, in order.
        write_to: The `--write-to` file, or None.

    Returns:
        What undoes it, for when the invocation ends.
    """
    token = _FILES.set(ConfigFiles(paths, write_to))
    return lambda: _FILES.reset(token)


def files() -> ConfigFiles:
    """The global `-f` and `--write-to`, as the services take them.

    Returns:
        The files this invocation reads and the one it edits; none outside a command.
    """
    return _FILES.get() or ConfigFiles([], None)


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
