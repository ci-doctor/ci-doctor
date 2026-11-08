"""The base every command derives from, and how a group registers them."""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

import typer


class Command(ABC):
    """A command. Its docstring is the `--help`; `run`'s parameters are its options."""

    name: ClassVar[str]

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - each command declares its own
        """Do the command."""


def register(app: typer.Typer, commands: Sequence[Command]) -> None:
    """Add `commands` to `app`, in `--help` order."""
    for command in commands:
        app.command(command.name, help=inspect.getdoc(command))(command.run)
