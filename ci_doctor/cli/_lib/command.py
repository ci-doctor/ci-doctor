"""The base every `ci-doctor` command derives from, and how a group adds its commands."""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

import typer


class Command(ABC):
    """One command: its name, its entry point, and the private helpers it needs.

    The subclass's own docstring is the command's `--help`. `run` is what typer calls:
    its parameters are the command's arguments and options; mark it `@override`.
    """

    #: The command as typed: `ci-doctor matchers <name>`.
    name: ClassVar[str]

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - each command declares its own
        """Do the command."""


def register(app: typer.Typer, commands: Sequence[Command]) -> None:
    """Add a group's commands to its typer app, each under its `name`.

    Explicit, not discovered: a group lists its commands, so a typo is an import
    error, not a command that silently goes missing.

    Args:
        app: The root app or a group's.
        commands: The group's commands, in `--help` order.
    """
    for command in commands:
        app.command(command.name, help=inspect.getdoc(command))(command.run)
