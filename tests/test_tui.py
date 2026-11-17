"""The shared TUI pieces in `cli/_console/tui/`, answered with real keystrokes."""

import io

import pytest

from ci_doctor.cli._console.tui import Cancelled, Option, components, pick_many, pick_one, yes_no

OPTIONS = [Option("a", "Alpha"), Option("b", "Beta", "detected"), Option("c", "Gamma")]
DOWN, SPACE, ENTER, CTRL_C = "\x1b[B", " ", "\r", "\x03"


def test_pick_many_starts_from_the_preselection(keys):
    """Enter keeps what is ticked; space on a row flips it."""
    keys.send_text(DOWN + DOWN + SPACE + ENTER)
    assert pick_many("Which?", OPTIONS, [1]) == ["b", "c"]


def test_pick_many_can_end_with_nothing(keys):
    """Unticking everything is an answer; the caller decides what it means."""
    keys.send_text(SPACE + ENTER)
    assert pick_many("Which?", OPTIONS, [0]) == []


def test_pick_one_starts_on_the_default(keys):
    """Enter picks the default; the arrows move off it."""
    keys.send_text(ENTER)
    assert pick_one("Where?", OPTIONS, default=1) == "b"
    keys.send_text(DOWN + ENTER)
    assert pick_one("Where?", OPTIONS) == "b"


@pytest.mark.parametrize(
    ("typed", "default", "expected"),
    [(ENTER, True, True), (ENTER, False, False), ("n", True, False), ("y", False, True)],
)
def test_yes_no(keys, typed, default, expected):
    """Enter takes the default; y and n answer at once."""
    keys.send_text(typed)
    assert yes_no("Go?", default=default) is expected


@pytest.mark.parametrize("ask", [lambda: pick_many("Which?", OPTIONS, [0]), lambda: yes_no("Go?")])
def test_ctrl_c_cancels(keys, ask):
    """Ctrl-C is a cancel, never a silent default."""
    keys.send_text(CTRL_C)
    with pytest.raises(Cancelled):
        ask()


def test_interactive_needs_a_terminal_on_both_ends(monkeypatch):
    """A pipe on either end means a script is running: no questions."""

    class Tty(io.StringIO):
        def __init__(self, tty):
            super().__init__()
            self.tty = tty

        def isatty(self):
            return self.tty

    monkeypatch.setattr("sys.stdin", Tty(True))
    monkeypatch.setattr("sys.stdout", Tty(True))
    assert components.interactive()
    monkeypatch.setattr("sys.stdout", Tty(False))
    assert not components.interactive()


def test_end_of_input_cancels(keys):
    """A closed terminal is a cancel too."""
    keys.close()
    with pytest.raises(Cancelled):
        yes_no("Go?")
