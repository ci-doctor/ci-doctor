"""A long document: paged with colour on a terminal, raw when piped."""

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
    """Page `text` highlighted as `lexer`; echo it raw under `--plain` or when piped."""
    if plain() or (not force and not sys.stdout.isatty()):
        typer.echo(text)  # not rich: it would pad every line to the terminal width
        return
    out = console(soft_wrap=True)
    with out.pager(LessPager(), styles=not no_color()):
        out.print(Syntax(text, lexer, theme="ansi_dark", background_color="default"))


class LessPager(Pager):
    """`$PAGER`, else `less -R`. Not rich's pager: pydoc prefers `$MANPAGER`, which may strip escapes."""

    def show(self, content: str) -> None:
        """Page `content`, or print it when the pager isn't installed."""
        cmd = shlex.split(os.environ.get("PAGER") or "less")
        if not cmd or not shutil.which(cmd[0]):
            sys.stdout.write(content)
            return
        env = {**os.environ, "LESS": f"{os.environ.get('LESS', '')} -R".strip()}
        subprocess.run(cmd, input=content, text=True, env=env, check=False)  # noqa: S603 - the user's own $PAGER
