"""`ci-doctor matchers …`: the registry author's commands and the user's `matchers:` editor.

Every command exits 1 on failure (a `SetupError`, invariant #3; a crash too, see
`_SetupGroup`), so a registry's CI step goes red on a stale index and a user typo never
silently edits the config. Commands that change the config touch only its `matchers:`
block (`config_edit.py`) and fetch before they write, so a bad registry or name leaves
the file as it was.
"""

import time
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import click
import httpx
import typer
from typer.core import TyperGroup

from ci_doctor.config.loader import anchor_registry, load_or_fail
from ci_doctor.config.schema import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.config_edit import edit_matchers, read_block
from ci_doctor.matchers.index import write_index
from ci_doctor.matchers.models import INDEX, Ref
from ci_doctor.matchers.registry import (
    Resolved,
    check_ref,
    fetch_index,
    fetch_matcher,
    index_for,
    redact,
    registry_dir,
    resolve,
    session,
    unknown_registry,
)
from ci_doctor.matchers.verify import verify as run_verify


class _SetupGroup(TyperGroup):
    """Every failure of a `matchers` command is a setup failure, a crash included.

    `main()` maps an unexpected exception to exit 0 so a crash never changes a
    pipeline's outcome (invariant #3). These commands never analyze anything: they
    *are* the registry's CI check, and a crash that exits 0 turns that check green.
    """

    def invoke(self, ctx: click.Context) -> object:
        """Run the command, turning any exception that is not already a usage or setup error into a `SetupError`."""
        try:
            return super().invoke(ctx)
        except (SetupError, click.ClickException, click.exceptions.Exit, click.Abort):
            raise
        except Exception as err:
            raise SetupError(f"internal error: {type(err).__name__}: {err}") from err


app = typer.Typer(cls=_SetupGroup, no_args_is_help=True, help="Browse, select and build matchers.")
registry_app = typer.Typer(no_args_is_help=True, help="Manage the registries matchers come from.")
app.add_typer(registry_app, name="registry")

_ROOT = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml).")
_CONFIG = typer.Option(
    Path(".ci-doctor.yml"), "--config", "-f", help="The config file to read and edit; created if missing."
)


# --- the registry author's side ----------------------------------------------------


@app.command()
def index(
    root: Path = _ROOT,
    check: bool = typer.Option(False, "--check", help="Write nothing; exit 1 if index.yaml is not current."),
) -> None:
    """Validate every matcher and bundle, then write index.yaml."""
    n = write_index(root, check=check)
    typer.echo(f"{root / INDEX}: {'current' if check else 'written'}, {n} entries")


@app.command()
def verify(
    root: Path = _ROOT,
    update: bool = typer.Option(
        False, "--update", help="Write each fixture's .expected from the current output instead of comparing."
    ),
) -> None:
    """Run every matcher over its fixtures and compare with the committed .expected files."""
    n = run_verify(root, update=update)
    typer.echo(f"{n} matchers verified" + (" (.expected files written; review the diff)" if update else ""))


# --- the user's side: edit and browse `matchers:` ---------------------------------


def _effective(path: Path) -> MatchersConfig:
    """The `matchers:` block as `analyze -f <path>` would see it; a missing file adds nothing.

    Args:
        path: The config file, which may not exist.

    Returns:
        The merged block.

    Raises:
        SetupError: If the config does not validate.
    """
    return load_or_fail([path] if path.is_file() else []).matchers


def _via(cfg: MatchersConfig, include: list[str], http: httpx.Client) -> dict[str, str]:
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


@app.command()
def add(
    refs: Annotated[
        list[str], typer.Argument(metavar="ALIAS/NAME...", help="Matchers or bundles to include.")
    ],
    config: Path = _CONFIG,
) -> None:
    """Add matchers or bundles to `matchers.include`."""
    cfg = _effective(config)
    current = list(read_block(config).get("include", []))
    seen = Resolved()
    new: list[str] = []
    with session() as http:
        parsed = [check_ref(r, cfg, http, seen) for r in refs]
        via = _via(cfg, current, http)
    for ref in map(str, parsed):
        if ref in current or ref in new:
            typer.echo(f"{ref}: already in include")
        elif via.get(ref, "direct") != "direct":
            typer.echo(f"{ref}: already included via {via[ref]}")
        else:
            new.append(ref)
    if new:
        edit_matchers(config, lambda m: m.__setitem__("include", [*current, *new]))
    for ref in new:
        typer.echo(f"added {ref}")


@app.command()
def remove(
    refs: Annotated[list[str], typer.Argument(metavar="ALIAS/NAME...", help="Entries to drop from include.")],
    config: Path = _CONFIG,
) -> None:
    """Remove matchers or bundles from `matchers.include`."""
    cfg = _effective(config)
    current = list(read_block(config).get("include", []))
    if missing := [r for r in refs if r not in current]:
        with session() as http:
            via = _via(cfg, current, http)
        for ref in missing:
            if (bundle := via.get(ref, "direct")) != "direct":
                raise SetupError(
                    f"{ref} is not in include itself, it comes from {bundle}: remove that instead"
                )
        raise SetupError(f"{missing[0]} is not in matchers.include")
    kept = [r for r in current if r not in refs]
    edit_matchers(config, lambda m: m.__setitem__("include", kept))
    for ref in dict.fromkeys(refs):
        typer.echo(f"removed {ref}")


@app.command("list")
def list_(config: Path = _CONFIG) -> None:
    """List what `matchers.include` resolves to, and how each matcher got in."""
    cfg = _effective(config)
    if not cfg.include:
        typer.echo("nothing included. Run `ci-doctor matchers add <registry>/<name>`.")
        return
    with session() as http:
        resolved = resolve(cfg, http)
    for key, via in resolved.matchers.items():
        ref = Ref.parse(key)
        tags = ", ".join(resolved.indexes[ref.alias].matchers[ref.name].classification)
        typer.echo(f"{key}  via {via}  [{tags}]")


@app.command()
def search(
    term: str = typer.Argument(..., help="Case-insensitive text to find."),
    registry: str | None = typer.Option(None, "--registry", help="Search only this registry."),
    config: Path = _CONFIG,
) -> None:
    """Search matcher and bundle names, descriptions and classifications."""
    cfg = _effective(config)
    needle = term.lower()
    aliases = [registry] if registry else sorted(cfg.registries)
    seen = Resolved()
    hits = 0
    with session() as http:
        for alias in aliases:
            served = index_for(cfg, alias, http, seen)
            for kind, entries in (("matcher", served.matchers), ("bundle", served.bundles)):
                for name, e in entries.items():
                    if needle in " ".join([name, e.description, *e.classification]).lower():
                        tags = f" [{', '.join(e.classification)}]" if e.classification else ""
                        typer.echo(f"{alias}/{name}  ({kind}){tags}  {e.description}")
                        hits += 1
    if not hits:
        typer.echo(f"no matchers match {term!r}")


def _history_url(location: str, path: str) -> str | None:
    """A link to a file's history when the registry is hosted on GitHub.

    Args:
        location: The registry's directory URL.
        path: The file, relative to it.

    Returns:
        The commits URL, or None for any other host.
    """
    parts = urlsplit(location)
    segs = [s for s in parts.path.split("/") if s]
    host = parts.hostname or ""
    # ponytail: a github.io site may serve a folder or branch other than the default of the
    # repo it is named after; this links the default branch and the user navigates from there.
    if host == "raw.githubusercontent.com" and len(segs) >= 3:
        owner, repo, branch, *sub = segs
        return f"https://github.com/{owner}/{repo}/commits/{branch}/{'/'.join([*sub, path])}"
    if host.endswith(".github.io"):
        owner = host.removesuffix(".github.io")
        repo = segs[0] if segs else host
        return f"https://github.com/{owner}/{repo}/commits/HEAD/{'/'.join([*segs[1:], path])}"
    return None


@app.command()
def info(ref: str = typer.Argument(..., metavar="ALIAS/NAME"), config: Path = _CONFIG) -> None:
    """Show one matcher or bundle: what it does, where it lives, what a bundle holds."""
    cfg = _effective(config)
    seen = Resolved()
    with session() as http:
        parsed = check_ref(ref, cfg, http, seen)
        served, location = seen.indexes[parsed.alias], seen.locations[parsed.alias]
        is_matcher = parsed.name in served.matchers
        entry = served.matchers[parsed.name] if is_matcher else served.bundles[parsed.name]
        typer.echo(f"{ref} ({'matcher' if is_matcher else 'bundle'})")
        typer.echo(f"description: {entry.description}")
        if is_matcher:
            typer.echo(f"classification: {', '.join(entry.classification)}")
            typer.echo(f"role: {fetch_matcher(parsed, seen, http).role}")
        typer.echo(f"registry: {redact(location)}")
        typer.echo(f"path: {entry.path}")
        if url := _history_url(location, entry.path):
            typer.echo(f"history: {url}")
        if not is_matcher:
            members = _via(cfg, [ref], http)
            typer.echo("matchers:\n" + "\n".join(f"  {m}" for m in members))


# --- `matchers registry` -----------------------------------------------------------


@registry_app.command("add")
def registry_add(
    pairs: Annotated[
        list[str], typer.Argument(metavar="NAME=LOCATION...", help="Registry alias and its folder.")
    ],
    config: Path = _CONFIG,
) -> None:
    """Add or override registries, after checking each serves a valid index.yaml."""
    cfg = _effective(config)
    new: dict[str, str] = {}
    for pair in pairs:
        name, eq, location = pair.partition("=")
        if not eq or not name or not location:
            raise SetupError(f"{pair!r} is not NAME=LOCATION")
        new[name] = location
    with session() as http:
        for name, location in new.items():
            fetch_index(name, registry_dir(name, anchor_registry(location, config.parent)), http)
    edit_matchers(config, lambda m: m.setdefault("registries", {}).update(new))
    for name, location in new.items():
        old = cfg.registries.get(name)
        typer.echo(
            f"{name}: {redact(old)} -> {redact(location)}" if old else f"{name}: added {redact(location)}"
        )


@registry_app.command("remove")
def registry_remove(
    names: Annotated[list[str], typer.Argument(metavar="NAME...", help="Registries to drop.")],
    config: Path = _CONFIG,
) -> None:
    """Remove registries from the config, unless `include` still uses them.

    Removing an override of a shipped registry (`official`) falls back to its default.
    """
    block = read_block(config)
    own, include = block.get("registries", {}), block.get("include", [])
    for name in names:
        if name not in own:
            raise SetupError(f"registry {name!r} is not in {config}")
        if used := [r for r in include if Ref.parse(r).alias == name]:
            raise SetupError(f"registry {name!r} is still used by matchers.include: {', '.join(used)}")

    def drop(m: dict) -> None:
        for name in names:
            m["registries"].pop(name, None)
        if not m["registries"]:
            del m["registries"]

    edit_matchers(config, drop)
    for name in dict.fromkeys(names):
        typer.echo(f"removed {name}")


@registry_app.command("list")
def registry_list(config: Path = _CONFIG) -> None:
    """List every configured registry."""
    for alias, location in _effective(config).registries.items():
        typer.echo(f"{alias}  {redact(location)}")


@registry_app.command()
def ping(
    names: Annotated[
        list[str] | None, typer.Argument(metavar="[NAME...]", help="Registries to check; all when omitted.")
    ] = None,
    config: Path = _CONFIG,
) -> None:
    """Fetch each registry's index and report reachability, latency and size."""
    cfg = _effective(config)
    if unknown := [n for n in names or [] if n not in cfg.registries]:
        raise unknown_registry(unknown[0], cfg.registries)
    down = 0
    with session() as http:
        for alias in names or sorted(cfg.registries):
            start = time.perf_counter()
            try:
                served = index_for(cfg, alias, http, Resolved())
            except SetupError as err:
                down += 1
                typer.echo(f"{alias}: FAILED {err}")
                continue
            ms = (time.perf_counter() - start) * 1000
            typer.echo(
                f"{alias}: ok, {ms:.0f} ms, {len(served.matchers)} matchers, {len(served.bundles)} bundles"
            )
    if down:
        raise SetupError(f"{down} registr{'y' if down == 1 else 'ies'} unreachable or invalid")
