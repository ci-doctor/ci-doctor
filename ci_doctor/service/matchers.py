"""Choosing matchers: search the registries, see what is included, add and remove.

Behind `ci-doctor matchers search | list | add | remove`, and the same four as MCP
tools, so a person and their agent select matchers by the same rules.
"""

from dataclasses import dataclass

from ci_doctor.config.files import ConfigFiles
from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.cache import Cache
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import Resolved, check_ref, index_for, resolve, session, via


@dataclass(frozen=True)
class Hit:
    """One matcher or bundle a search found.

    Attributes:
        name: Its `alias/name`, as `matchers.include` takes it.
        kind: `matcher` or `bundle`.
        tags: The matcher's tags; empty for a bundle.
        description: What it covers, in its author's words.
    """

    name: str
    kind: str
    tags: list[str]
    description: str


@dataclass(frozen=True)
class Included:
    """One matcher `matchers.include` resolves to.

    Attributes:
        matcher: Its `alias/name`.
        via: `direct`, or the top-level bundle that brought it in.
        tags: Its tags.
    """

    matcher: str
    via: str
    tags: list[str]


@dataclass(frozen=True)
class Skipped:
    """A ref `add` left out, and why.

    Attributes:
        ref: The `alias/name` as given.
        why: The reason, for the caller to show.
    """

    ref: str
    why: str


@dataclass(frozen=True)
class AddResult:
    """What one `add` did.

    Attributes:
        added: The refs written to `matchers.include`, in the order given.
        skipped: The refs that were already selected.
    """

    added: list[str]
    skipped: list[Skipped]


def search(cfg: MatchersConfig, term: str, registry: str | None = None) -> list[Hit]:
    """Find matchers and bundles by name, description or tag.

    Args:
        cfg: The `matchers:` config block.
        term: Case-insensitive text to find; empty matches everything.
        registry: Search only this registry, or None for every configured one.

    Returns:
        The hits, registry by registry, matchers before bundles.

    Raises:
        SetupError: On an unknown registry, or an index that can't be fetched or validated.
    """
    needle = term.lower()
    seen = Resolved(cache=Cache.open(cfg))
    hits = []
    with session(cfg) as http:
        for alias in [registry] if registry else sorted(cfg.registries):
            served = index_for(cfg, alias, http, seen)
            for kind, entries in (("matcher", served.matchers), ("bundle", served.bundles)):
                for name, e in entries.items():
                    if needle in " ".join([name, e.description, *e.tags]).lower():
                        hits.append(Hit(f"{alias}/{name}", kind, e.tags, e.description))
    return hits


def included(cfg: MatchersConfig) -> list[Included]:
    """What `matchers.include` resolves to, and how each matcher got in.

    Args:
        cfg: The `matchers:` config block.

    Returns:
        One entry per matcher, in include order; empty when nothing is included.

    Raises:
        SetupError: On anything `resolve` rejects.
    """
    if not cfg.include:
        return []
    with session(cfg) as http:
        resolved = resolve(cfg, http)
    rows = []
    for key, how in resolved.matchers.items():
        ref = Ref.parse(key)
        rows.append(Included(key, how, resolved.indexes[ref.alias].matchers[ref.name].tags))
    return rows


def add(files: ConfigFiles, refs: list[str]) -> AddResult:
    """Add matchers or bundles to the target file's `matchers.include`.

    Args:
        files: The config read, and the file edited.
        refs: `alias/name` entries to include.

    Returns:
        What was added and what was already selected.

    Raises:
        SetupError: On a ref no configured registry serves; nothing is then written.
    """
    cfg = files.editable().matchers
    current = list(files.read("matchers").get("include", []))
    seen = Resolved(cache=Cache.open(cfg))
    with session(cfg) as http:
        parsed = [str(check_ref(r, cfg, http, seen)) for r in refs]
        brought_by = via(cfg, current, http)
    new: list[str] = []
    skipped = []
    for ref in parsed:
        if ref in current or ref in new:
            skipped.append(Skipped(ref, "already in include"))
        elif (bundle := brought_by.get(ref, "direct")) != "direct":
            skipped.append(Skipped(ref, f"already included via {bundle}"))
        else:
            new.append(ref)
    if new:
        files.edit("matchers", lambda m: m.__setitem__("include", [*current, *new]))
    return AddResult(new, skipped)


def remove(files: ConfigFiles, refs: list[str]) -> list[str]:
    """Drop entries from the target file's `matchers.include`.

    Args:
        files: The config read, and the file edited.
        refs: `alias/name` entries to drop.

    Returns:
        The entries removed, deduplicated, in the order given.

    Raises:
        SetupError: If a ref is not in the file's own include, naming the bundle
            that brings it when one does; nothing is then written.
    """
    cfg = files.editable().matchers
    current = list(files.read("matchers").get("include", []))
    if missing := [r for r in refs if r not in current]:
        raise _not_included(missing[0], cfg, current)
    files.edit("matchers", lambda m: m.__setitem__("include", [r for r in current if r not in refs]))
    return list(dict.fromkeys(refs))


def _not_included(ref: str, cfg: MatchersConfig, include: list[str]) -> SetupError:
    """The error for removing a ref the file does not list, naming the bundle that brings it.

    Args:
        ref: The ref as given.
        cfg: The `matchers:` block, to expand `include` with.
        include: The file's own `matchers.include`.

    Returns:
        The error, for the caller to raise.
    """
    with session(cfg) as http:
        bundle = via(cfg, include, http).get(ref, "direct")
    if bundle != "direct":
        return SetupError(f"{ref} is not in include itself, it comes from {bundle}: remove that instead")
    return SetupError(f"{ref} is not in matchers.include")
