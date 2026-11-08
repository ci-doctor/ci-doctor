"""One line of output."""

import typer
from rich.text import Text

from ci_doctor.cli._lib.terminal import console, plain


def say(text: str, style: str = "") -> None:
    """Print one line, styled unless `--plain`. Never read as rich markup."""
    if plain():
        typer.echo(text)
    else:
        console().print(Text(text, style=style))


def done(text: str) -> None:
    """Report a change made."""
    say(text if plain() else f"✓ {text}", "green")


def skipped(text: str) -> None:
    """Report something left as it was."""
    say(text if plain() else f"· {text}", "dim")
