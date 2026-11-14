"""The base every command derives from, and how a group registers them."""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

import typer


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
            command.run
        )


def _examples(lines: Sequence[str]) -> str | None:
    """The `--help` epilog. Typer joins lines inside a paragraph, so each example is its own."""
    return "\n\n".join(["Examples:", *lines]) if lines else None
