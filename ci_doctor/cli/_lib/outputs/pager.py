"""A long document (the config, a schema): paged with colour on a terminal, raw when piped."""

import os
import shlex
import shutil
import subprocess
import sys

import typer
from rich.pager import Pager
from rich.syntax import Syntax

from ci_doctor.cli._lib.terminal import console, no_color, plain


def page(text: str, lexer: str, *, force: bool = False) -> None:
    """Show `text` highlighted in a pager, or echo it raw under `--plain` or when piped.

    Args:
        text: The document.
        lexer: How to highlight it (`yaml`, `json`, `diff`).
        force: Page even when stdout is not a terminal.
    """
    if plain() or (not force and not sys.stdout.isatty()):
        # Raw echo, not rich: piped output is meant to be re-parsed, and a Console
        # would pad every line to the detected terminal width.
        typer.echo(text)
        return
    out = console(soft_wrap=True)  # soft_wrap: never fold long regexes
    with out.pager(LessPager(), styles=not no_color()):
        out.print(Syntax(text, lexer, theme="ansi_dark", background_color="default"))


class LessPager(Pager):
    """`$PAGER`, else `less`, with less told to pass the colour escapes through.

    Not rich's own pager: it defers to pydoc, which prefers `$MANPAGER`, and a
    MANPAGER like `col -bx | bat` strips the escape bytes and prints the rest.
    """

    def show(self, content: str) -> None:
        """Page `content`, or print it when the pager isn't installed.

        Args:
            content: The rendered text, escapes included.
        """
        cmd = shlex.split(os.environ.get("PAGER") or "less")
        if not cmd or not shutil.which(cmd[0]):
            sys.stdout.write(content)
            return
        env = {**os.environ, "LESS": f"{os.environ.get('LESS', '')} -R".strip()}
        subprocess.run(cmd, input=content, text=True, env=env, check=False)  # noqa: S603 - the user's own $PAGER
