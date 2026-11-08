"""The base every `ci-doctor` command derives from."""

import inspect
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import typer


class Command(ABC):
    """One command: its name, its entry point, and the private helpers it needs.

    The subclass's own docstring is the command's `--help`. `run` is what typer calls:
    its parameters are the command's arguments and options; mark it `@override`. A
    group registers its commands explicitly (`PingCommand.register(app)`), so a typo is
    an import error, not a command that silently goes missing.
    """

    #: The command as typed: `ci-doctor matchers <name>`.
    name: ClassVar[str]

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - each command declares its own
        """Do the command."""

    @classmethod
    def register(cls, app: typer.Typer) -> None:
        """Add the command to a typer app, under its `name`.

        Args:
            app: The root app or a group's.
        """
        app.command(cls.name, help=inspect.getdoc(cls))(cls().run)
