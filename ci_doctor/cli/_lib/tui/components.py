"""Asking the user: pick several, pick one, yes or no. Plain `rich` prompts, no full-screen UI.

A command asks through a `Prompter`, so a test can script the answers. Every question
loops on a bad answer and raises `Cancelled` on Ctrl-C or end of input; the command
decides what cancelling means (for an edit: nothing written, exit 0).
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from rich.prompt import Prompt
from rich.text import Text

from ci_doctor.cli._lib.terminal import console, plain

T = TypeVar("T")


class Cancelled(Exception):
    """The user left a question with Ctrl-C or end of input."""


@dataclass(frozen=True)
class Option(Generic[T]):
    """One choice in a list.

    Attributes:
        value: What picking it returns.
        label: Its name in the list.
        hint: A dim note after the label, e.g. "detected".
    """

    value: T
    label: str
    hint: str = ""


class Prompter(Protocol):
    """Where questions go and answers come from: the terminal, or a test's script."""

    def show(self, text: str, style: str = "") -> None:
        """Print one line; never read as markup."""
        ...

    def ask(self, question: str, default: str = "") -> str:
        """Read one answer; `default` when the user just presses Enter.

        Raises:
            Cancelled: On Ctrl-C or end of input.
        """
        ...


class RichPrompter:
    """The terminal, through `rich.prompt.Prompt`, honouring `--no-color`."""

    def __init__(self) -> None:
        """Use the stdout console."""
        self._console = console()

    def show(self, text: str, style: str = "") -> None:
        self._console.print(Text(text, style=style))

    def ask(self, question: str, default: str = "") -> str:
        try:
            return Prompt.ask(
                Text(question), console=self._console, default=default, show_default=bool(default)
            )
        except (KeyboardInterrupt, EOFError):
            self._console.print()
            raise Cancelled from None


def interactive() -> bool:
    """Whether a person can answer: a terminal on both ends, and not `--plain` (meant for scripts)."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not plain()


def pick_many(
    prompter: Prompter, question: str, options: Sequence[Option[T]], selected: Sequence[int]
) -> list[T]:
    """Let the user tick any number of options, by typing their numbers.

    Args:
        prompter: Where to ask.
        question: The heading above the list.
        options: The choices, numbered from 1 in this order.
        selected: Indexes ticked to begin with.

    Returns:
        The picked values, in list order; empty when the user unticks everything.

    Raises:
        Cancelled: On Ctrl-C or end of input.
    """
    ticked = set(selected)
    width = max((len(o.label) for o in options), default=0)
    while True:
        prompter.show("")
        prompter.show(question, "bold")
        for i, option in enumerate(options):
            mark = "x" if i in ticked else " "
            prompter.show(f"  {i + 1:>2}  [{mark}] {option.label:<{width}}  {option.hint}".rstrip())
        answer = prompter.ask('Type numbers to toggle (e.g. 2 3), "all", "none", or Enter to continue')
        if not answer.strip():
            return [o.value for i, o in enumerate(options) if i in ticked]
        try:
            ticked = toggle(ticked, answer, len(options))
        except ValueError as err:
            prompter.show(str(err), "yellow")


def toggle(ticked: set[int], answer: str, count: int) -> set[int]:
    """Apply one answer to the ticked set: numbers flip, `all` and `none` set everything.

    Args:
        ticked: Indexes ticked now.
        answer: What the user typed, e.g. "2 3" or "2,3".
        count: How many options there are.

    Returns:
        The new ticked set.

    Raises:
        ValueError: If a word is not a number in range, `all` or `none`; the message says so.
    """
    words = answer.replace(",", " ").lower().split()
    if words == ["all"]:
        return set(range(count))
    if words == ["none"]:
        return set()
    result = set(ticked)
    for word in words:
        if not word.isdigit() or not 1 <= int(word) <= count:
            raise ValueError(f'{word!r}: type numbers from 1 to {count}, "all" or "none"')
        result ^= {int(word) - 1}
    return result


def pick_one(prompter: Prompter, question: str, options: Sequence[Option[T]], default: int = 0) -> T:
    """Let the user pick exactly one option by its number.

    Args:
        prompter: Where to ask.
        question: The heading above the list.
        options: The choices, numbered from 1 in this order.
        default: The index Enter picks.

    Returns:
        The picked value.

    Raises:
        Cancelled: On Ctrl-C or end of input.
    """
    width = max((len(o.label) for o in options), default=0)
    prompter.show("")
    prompter.show(question, "bold")
    for i, option in enumerate(options):
        prompter.show(f"  {i + 1:>2}  {option.label:<{width}}  {option.hint}".rstrip())
    while True:
        answer = prompter.ask(f"Choose 1-{len(options)}", str(default + 1)).strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1].value
        prompter.show(f"{answer!r}: type a number from 1 to {len(options)}", "yellow")


def yes_no(prompter: Prompter, question: str, *, default: bool = True) -> bool:
    """Ask a yes-or-no question.

    Args:
        prompter: Where to ask.
        question: The question.
        default: What Enter answers.

    Returns:
        The answer.

    Raises:
        Cancelled: On Ctrl-C or end of input.
    """
    while True:
        answer = prompter.ask(f"{question} [y/n]", "y" if default else "n").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        prompter.show(f"{answer!r}: type y or n", "yellow")
