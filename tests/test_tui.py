"""The shared TUI pieces in `cli/_lib/tui/components.py`, answered from a script."""

import io

import pytest

from ci_doctor.cli._lib.tui import Cancelled, Option, components, pick_many, pick_one, toggle, yes_no

OPTIONS = [Option("a", "Alpha"), Option("b", "Beta", "detected"), Option("c", "Gamma")]


class Script:
    """A `Prompter` that answers from a list and records every line shown."""

    def __init__(self, *answers):
        """Answer with `answers` in order; an empty string takes the question's default."""
        self.answers = list(answers)
        self.shown: list[str] = []

    def show(self, text, style=""):
        """Record the line."""
        self.shown.append(text)

    def ask(self, question, default=""):
        """The next answer; Cancelled once they run out, like end of input."""
        if not self.answers:
            raise Cancelled
        answer = self.answers.pop(0)
        return default if answer == "" else answer


@pytest.mark.parametrize(
    ("ticked", "answer", "expected"),
    [
        ({0}, "2 3", {0, 1, 2}),
        ({0, 1}, "1", {1}),
        ({0}, "2,3", {0, 1, 2}),
        (set(), "all", {0, 1, 2}),
        ({0, 2}, "NONE", set()),
        ({0}, "2 2", {0}),
    ],
)
def test_toggle_flips_numbers_and_sets_all_or_none(ticked, answer, expected):
    """Numbers flip, commas count as spaces, `all` and `none` replace the set."""
    assert toggle(ticked, answer, 3) == expected


@pytest.mark.parametrize("answer", ["0", "4", "x", "1 two", "all 1"])
def test_toggle_refuses_what_is_not_a_choice(answer):
    """Out of range, a word, or `all` mixed with numbers: the message names the choices."""
    with pytest.raises(ValueError, match='type numbers from 1 to 3, "all" or "none"'):
        toggle({0}, answer, 3)


def test_pick_many_starts_from_the_preselection_and_redraws_after_each_toggle():
    """Enter keeps what is ticked; a toggle redraws the list with the new marks."""
    script = Script("3", "")
    assert pick_many(script, "Which?", OPTIONS, [1]) == ["b", "c"]
    assert "   2  [x] Beta   detected" in script.shown
    assert script.shown.count("   3  [x] Gamma") == 1


def test_pick_many_says_what_was_wrong_and_asks_again():
    """A bad answer changes nothing and shows the hint."""
    script = Script("9", "")
    assert pick_many(script, "Which?", OPTIONS, [0]) == ["a"]
    assert any("'9': type numbers from 1 to 3" in line for line in script.shown)


def test_pick_many_can_end_with_nothing():
    """Unticking everything is an answer; the caller decides what it means."""
    assert pick_many(Script("none", ""), "Which?", OPTIONS, [0, 1]) == []


def test_pick_one_takes_the_default_on_enter_and_a_number_otherwise():
    """Enter picks the default; a bad number asks again."""
    assert pick_one(Script(""), "Where?", OPTIONS, default=1) == "b"
    script = Script("7", "3")
    assert pick_one(script, "Where?", OPTIONS) == "c"
    assert any("'7': type a number from 1 to 3" in line for line in script.shown)


@pytest.mark.parametrize(
    ("answers", "default", "expected"),
    [((""), True, True), ((""), False, False), (("n"), True, False), (("YES"), False, True)],
)
def test_yes_no(answers, default, expected):
    """Enter takes the default; y, yes, n and no in any case."""
    assert yes_no(Script(answers), "Go?", default=default) is expected


def test_yes_no_asks_again_on_anything_else():
    """Anything but y or n shows the hint and asks again."""
    script = Script("maybe", "y")
    assert yes_no(script, "Go?") is True
    assert any("'maybe': type y or n" in line for line in script.shown)


def test_running_out_of_answers_cancels():
    """End of input is a cancel, never a silent default."""
    with pytest.raises(Cancelled):
        pick_many(Script(), "Which?", OPTIONS, [0])


def test_the_rich_prompter_reads_the_terminal_and_cancels_on_end_of_input(monkeypatch):
    """The real prompter: typed lines answer, and end of input raises Cancelled."""
    monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
    prompter = components.RichPrompter()
    assert pick_one(prompter, "Where?", OPTIONS) == "b"
    with pytest.raises(Cancelled):
        prompter.ask("again?")


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
