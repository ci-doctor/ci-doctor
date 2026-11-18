"""The base every command derives from, and how a group registers them."""

import functools
import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

import typer

from ci_doctor.errors import SetupError

log = logging.getLogger("ci_doctor.cli")


class Command(ABC):
    """A command. Its docstring is the `--help`; `run`'s parameters are its options."""

    name: ClassVar[str]
    #: Command lines shown under "Examples" in `--help`, each with an optional `# note`.
    examples: ClassVar[Sequence[str]] = ()

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - each command declares its own
        """Do the command."""


def register(app: typer.Typer, commands: Sequence[Command]) -> None:
    """Add `commands` to `app`, in `--help` order.

    Args:
        app: The root app or a group's.
        commands: The group's commands, in `--help` order.
    """
    for command in commands:
        app.command(command.name, help=inspect.getdoc(command), epilog=_examples(command.examples))(
            _exit_one_on_failure(command.run)
        )


def _exit_one_on_failure(run: Callable[..., None]) -> Callable[..., None]:
    """Any failure is one log line and exit 1, not a traceback; `analyze` keeps invariant #3 itself."""

    @functools.wraps(run)
    def guarded(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - the command's own parameters
        try:
            run(*args, **kwargs)
        except SetupError as exc:
            log.error(exc)
            raise typer.Exit(1) from None
        except Exception as exc:  # noqa: BLE001 - a crash is reported, not raised
            log.error(f"internal error: {type(exc).__name__}: {exc}")
            raise typer.Exit(1) from None

    return guarded


def _examples(lines: Sequence[str]) -> str | None:
    """The `--help` epilog. Typer joins lines inside a paragraph, so each example is its own."""
    return "\n\n".join(["Examples:", *lines]) if lines else None
