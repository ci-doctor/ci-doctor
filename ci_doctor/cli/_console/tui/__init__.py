"""The terminal UI: questions a command asks a person, as inline `questionary` prompts.

Generic only: no command's flow lives here. A command builds its questions from these
pieces in its own private members (`agent install` is the example).
"""

from ci_doctor.cli._console.tui.components import (
    Cancelled,
    Option,
    interactive,
    pick_many,
    pick_one,
    yes_no,
)

__all__ = [
    "Cancelled",
    "Option",
    "interactive",
    "pick_many",
    "pick_one",
    "yes_no",
]
