"""What several `matchers` commands share: the config they read, and how they print.

Output is rich by default: tables, and a mark on each change. `ci-doctor matchers
--plain …` prints one bare line per row instead, for scripts and `grep`.
"""

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import click
import httpx
import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ci_doctor.config.loader import load_config
from ci_doctor.config.schema import MatchersConfig
from ci_doctor.matchers.registry import resolve

ROOT = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml).")
#: `ctx.meta` key the `matchers` group sets from `--plain`.
PLAIN = "ci_doctor.matchers.plain"
_DEFAULT_CONFIG = Path(".ci-doctor.yml")
#: No highlighting: it would colour numbers and paths inside registry data at random.
_console = Console(highlight=False)

Cell = str | Text


def config_files() -> list[Path]:
    """The global `ci-doctor -f` files, lowest precedence first.

    Returns:
        The files, empty when none were given.
    """
    return click.get_current_context().obj or []


def target() -> Path:
    """The file a command that edits the config writes: the last `-f`, else `./.ci-doctor.yml`.

    Returns:
        The path, which may not exist yet.
    """
    files = config_files()
    return files[-1] if files else _DEFAULT_CONFIG


def effective() -> MatchersConfig:
    """The `matchers:` block as `analyze` would see it; a missing target adds nothing.

    Returns:
        The merged block.

    Raises:
        SetupError: If the config does not validate, or a file other than the target is missing.
    """
    edited = target()
    return load_config([p for p in config_files() if p != edited or p.is_file()]).matchers


def via(cfg: MatchersConfig, include: list[str], http: httpx.Client) -> dict[str, str]:
    """Which top-level entry brings in each matcher.

    Args:
        cfg: The effective block (for its registries).
        include: The refs to expand.
        http: The command's HTTP client.

    Returns:
        `alias/name` -> `"direct"` or the bundle; empty when `include` is.
    """
    if not include:
        return {}
    return resolve(MatchersConfig(registries=cfg.registries, include=include), http).matchers


def _plain() -> bool:
    return click.get_current_context().meta.get(PLAIN, False)


def say(text: str, style: str = "") -> None:
    """Print one line, styled unless `--plain`.

    Args:
        text: The line; never read as rich markup.
        style: A rich style for the pretty output.
    """
    if _plain():
        typer.echo(text)
    else:
        _console.print(Text(text, style=style))


def done(text: str) -> None:
    """Report a change made.

    Args:
        text: What changed.
    """
    say(text if _plain() else f"✓ {text}", "green")


def skipped(text: str) -> None:
    """Report something left as it was.

    Args:
        text: What, and why.
    """
    say(text if _plain() else f"· {text}", "dim")


def table(
    columns: Sequence[str], rows: Iterable[Sequence[Cell]], line: Callable[..., str], *, header: bool = True
) -> None:
    """Print rows as a table, or under `--plain` as one `line(*cells)` each.

    Args:
        columns: The header.
        rows: The cells; a `Text` keeps its style in the table.
        line: Formats one row's plain-text cells for `--plain`.
        header: Show the column names; off for a key-value listing.
    """
    if _plain():
        for row in rows:
            typer.echo(line(*map(str, row)))  # str(Text) is its plain text
        return
    out = Table(*columns, box=box.SIMPLE_HEAD if header else None, header_style="bold", show_header=header)
    for row in rows:
        out.add_row(*(Text(c) if isinstance(c, str) else c for c in row))
    _console.print(out)
