"""Asking the user: pick several, pick one, yes or no. Inline `questionary` prompts, no full-screen UI.

questionary is imported on first use: it costs ~150ms, and most commands never ask.
Every question raises `Cancelled` on Ctrl-C or end of input; the command decides what
cancelling means (for an edit: nothing written, exit 0).
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from ci_doctor.cli._console.terminal import no_color, plain

if TYPE_CHECKING:
    from questionary import Choice, Question

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


def interactive() -> bool:
    """Whether a person can answer: a terminal on both ends, and not `--plain` (meant for scripts)."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not plain()


def pick_many(question: str, options: Sequence[Option[T]], selected: Sequence[int]) -> list[T]:
    """Let the user tick any number of options: arrows to move, space to tick, Enter to go on.

    Args:
        question: The heading above the list.
        options: The choices, in this order.
        selected: Indexes ticked to begin with.

    Returns:
        The picked values, in list order; empty when the user unticks everything.

    Raises:
        Cancelled: On Ctrl-C or end of input.
    """
    import questionary

    choices = [_choice(o, checked=i in selected) for i, o in enumerate(options)]
    return _ask(questionary.checkbox(question, choices, **_style()))


def pick_one(question: str, options: Sequence[Option[T]], default: int = 0) -> T:
    """Let the user pick exactly one option with the arrows and Enter.

    Args:
        question: The heading above the list.
        options: The choices, in this order.
        default: The index the cursor starts on.

    Returns:
        The picked value.

    Raises:
        Cancelled: On Ctrl-C or end of input.
    """
    import questionary

    choices = [_choice(o) for o in options]
    return _ask(questionary.select(question, choices, default=choices[default], **_style()))


def yes_no(question: str, *, default: bool = True) -> bool:
    """Ask a yes-or-no question; `y` or `n` answers at once, Enter takes the default.

    Args:
        question: The question.
        default: What Enter answers.

    Returns:
        The answer.

    Raises:
        Cancelled: On Ctrl-C or end of input.
    """
    import questionary

    return _ask(questionary.confirm(question, default=default, **_style()))


def _choice(option: Option, *, checked: bool = False) -> "Choice":
    from questionary import Choice

    title = [("", option.label), ("class:instruction", f"  {option.hint}")] if option.hint else option.label
    return Choice(title, value=option.value, checked=checked)


def _style() -> dict[str, Any]:
    """No colour under `--no-color`."""
    from prompt_toolkit.output import ColorDepth

    return {"color_depth": ColorDepth.MONOCHROME} if no_color() else {}


def _ask(question: "Question") -> Any:  # noqa: ANN401 - the prompt's own answer type
    try:
        answer = question.unsafe_ask()
    except (KeyboardInterrupt, EOFError):
        raise Cancelled from None
    if answer is None:
        raise Cancelled
    return answer
