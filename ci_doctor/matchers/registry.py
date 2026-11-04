"""The user's side of a registry: resolving `matchers.include` and loading what it selects.

Every `analyze` resolves `matchers.include` against the registries' indexes and
fetches what it selected: no cache, so a registry is always read as it is now.
What comes back is someone else's file, so every fetch is bounded (scheme,
redirects, size, path containment, in `transports/`) and every file is validated
again (`validate.py`). Anything wrong is a `SetupError`: the job exits 1 before
analysis starts, naming the registry and the cause.

The author's side, building `index.yaml`, is `index.py`.
"""

import contextlib
import difflib
import logging
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError

from ci_doctor.config.schema import MatcherConfig, MatchersConfig, unqualified
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import INDEX, MAX_BUNDLE_DEPTH, Index, Ref
from ci_doctor.matchers.transports import redact, transport_for
from ci_doctor.matchers.validate import bundle_from, matcher_from, parse_yaml

log = logging.getLogger("ci_doctor.matchers")

TIMEOUT = 10.0
INDEX_CAP = 1024 * 1024
FILE_CAP = 64 * 1024
EMPTY_INCLUDE = (
    "no matchers or bundles selected. Run `ci-doctor matchers add official/<name>` "
    "or browse https://matchers.cidoc.dev"
)


def _fail(alias: str, location: str, cause: object) -> SetupError:
    """The one shape every loading error takes: registry, where, why — redacted.

    Args:
        alias: The registry's alias.
        location: Where it was read from.
        cause: What went wrong.

    Returns:
        The error, for the caller to raise.
    """
    return SetupError(redact(f"registry {alias!r} ({location}): {cause}"))


def registry_dir(alias: str, value: str) -> str:
    """Normalise a configured registry location to the directory holding `index.yaml`.

    Args:
        alias: The registry's alias, for errors.
        value: As written under `matchers.registries`.

    Returns:
        The location its transport reads from: an `http(s)://` URL ending in `/`,
        a `git+` location as written, or a local directory path.

    Raises:
        SetupError: On a location naming the index file, or one no transport accepts.
    """
    if value.rstrip("/").endswith(".yaml"):
        raise _fail(alias, value, "point at the registry folder, not the index file")
    try:
        return transport_for(value).locate(value)
    except SetupError as err:
        raise _fail(alias, value, err) from None


def fetch_file(alias: str, location: str, rel: str, *, cap: int, client: httpx.Client) -> str:
    """Read one file of a registry through the transport its location calls for.

    Args:
        alias: The registry's alias, for errors.
        location: Its directory, from :func:`registry_dir`.
        rel: The file, relative to that directory (already path-checked by `Index`).
        cap: The most bytes accepted.
        client: The run's shared HTTP client.

    Returns:
        The file's text.

    Raises:
        SetupError: If it can't be read, is over `cap`, or leaves the registry.
    """
    try:
        return transport_for(location).read(alias, location, rel, cap=cap, client=client)
    except SetupError as err:
        raise _fail(alias, location, err) from None


def fetch_index(alias: str, location: str, client: httpx.Client) -> Index:
    """Fetch and validate a registry's `index.yaml`.

    Args:
        alias: The registry's alias.
        location: Its directory, from :func:`registry_dir`.
        client: The run's shared HTTP client.

    Returns:
        The validated index: entry count, names and path rules already checked.

    Raises:
        SetupError: If it can't be fetched, parsed or validated.
    """
    text = fetch_file(alias, location, INDEX, cap=INDEX_CAP, client=client)
    data = parse_yaml(text, redact(f"registry {alias!r} ({location}): {INDEX}"))
    try:
        return Index.model_validate(data)
    except ValidationError as err:
        raise _fail(alias, location, f"{INDEX}: {err}") from None


@contextlib.contextmanager
def session(client: httpx.Client | None = None) -> Iterator[httpx.Client]:
    """The HTTP client for one run: the caller's, or a fresh one closed afterwards.

    Args:
        client: A client to reuse (tests pass one over `httpx.MockTransport`).

    Yields:
        The client.
    """
    if client is not None:
        yield client
        return
    with httpx.Client(timeout=TIMEOUT) as fresh:
        yield fresh


@dataclass
class Resolved:
    """Registries read so far, and what `matchers.include` expands to.

    Also the per-command cache of the CLI's lookups: an alias's index is fetched
    into it once, however many references name that alias.

    Attributes:
        locations: Each referenced registry's directory, by alias.
        indexes: Each referenced registry's index, by alias.
        matchers: Every selected `alias/name`, in include order, mapped to how it
            got in: `"direct"`, or the top-level bundle that pulled it in.
    """

    locations: dict[str, str] = field(default_factory=dict)
    indexes: dict[str, Index] = field(default_factory=dict)
    matchers: dict[str, str] = field(default_factory=dict)


def unknown_registry(alias: str, registries: Mapping[str, str]) -> SetupError:
    """The error for an alias no registry is configured under.

    Args:
        alias: The alias as written.
        registries: The configured registries.

    Returns:
        The error, listing the configured aliases, for the caller to raise.
    """
    known = ", ".join(sorted(registries)) or "none"
    return SetupError(f"unknown registry {alias!r} (configured: {known})")


def _not_served(ref: Ref, index: Index) -> SetupError:
    """The error for a name its registry does not serve, with the closest it does.

    Args:
        ref: The missing reference.
        index: Its registry's index.

    Returns:
        The error, for the caller to raise.
    """
    hint = difflib.get_close_matches(ref.name, [*index.matchers, *index.bundles], n=1)
    suggestion = f"; did you mean {ref.alias}/{hint[0]}?" if hint else ""
    return SetupError(f"{ref} is not in registry {ref.alias!r}{suggestion}")


def index_for(cfg: MatchersConfig, alias: str, http: httpx.Client, seen: Resolved) -> Index:
    """Locate a configured registry and fetch its index, once per `seen`.

    Args:
        cfg: The `matchers:` config block.
        alias: A registry alias.
        http: The run's HTTP client.
        seen: Registries already read; filled in with this one.

    Returns:
        The registry's index.

    Raises:
        SetupError: On an unknown alias, or an index that can't be fetched or validated.
    """
    if alias not in seen.indexes:
        if alias not in cfg.registries:
            raise unknown_registry(alias, cfg.registries)
        seen.locations[alias] = registry_dir(alias, cfg.registries[alias])
        seen.indexes[alias] = fetch_index(alias, seen.locations[alias], http)
    return seen.indexes[alias]


def check_ref(value: str, cfg: MatchersConfig, http: httpx.Client, seen: Resolved) -> Ref:
    """Parse `alias/name` as typed and prove its registry serves it.

    Args:
        value: As typed on the command line.
        cfg: The `matchers:` config block.
        http: The run's HTTP client.
        seen: Registries already read.

    Returns:
        The parsed reference.

    Raises:
        SetupError: On a bad shape, an unknown alias, or an unknown name (with a "did you mean").
    """
    if "/" not in value:
        raise SetupError(unqualified(value))
    ref = Ref.parse(value)
    index = index_for(cfg, ref.alias, http, seen)
    if ref.name not in index.matchers and ref.name not in index.bundles:
        raise _not_served(ref, index)
    return ref


def _fetch_entry(ref: Ref, seen: Resolved, http: httpx.Client) -> tuple[Any, str]:
    """Fetch and parse the file one served name points at.

    Args:
        ref: A matcher or bundle its registry's index lists.
        seen: Registries already read, holding `ref`'s.
        http: The run's HTTP client.

    Returns:
        The parsed document, and where it came from (redacted) for error messages.

    Raises:
        SetupError: If the file can't be fetched or isn't valid YAML.
    """
    index, location = seen.indexes[ref.alias], seen.locations[ref.alias]
    entry = index.matchers.get(ref.name) or index.bundles[ref.name]
    where = redact(f"registry {ref.alias!r} ({location}): {entry.path}")
    text = fetch_file(ref.alias, location, entry.path, cap=FILE_CAP, client=http)
    return parse_yaml(text, where), where


def fetch_matcher(ref: Ref, seen: Resolved, http: httpx.Client) -> MatcherConfig:
    """Fetch and validate one matcher its registry serves, still under its bare id.

    Args:
        ref: A matcher its registry's index lists.
        seen: Registries already read, holding `ref`'s.
        http: The run's HTTP client.

    Returns:
        The matcher.

    Raises:
        SetupError: If it can't be fetched, fails a load-time rule, or its id is not its index name.
    """
    data, where = _fetch_entry(ref, seen, http)
    m = matcher_from(data, where)
    if m.id != ref.name:
        raise SetupError(f"{where}: id {m.id!r} does not match its index name {ref.name!r}")
    return m


def resolve(cfg: MatchersConfig, client: httpx.Client | None = None) -> Resolved:
    """Expand `matchers.include` into matcher names, fetching only indexes and bundles.

    Args:
        cfg: The `matchers:` config block.
        client: The run's HTTP client, or None for a fresh one.

    Returns:
        The expansion, deduplicated: a matcher listed directly and in a bundle loads once.

    Raises:
        SetupError: On an empty selection, an unknown alias or name, a bundle
            cycle or over-deep nesting, or any fetch or validation failure.
    """
    if not cfg.include:
        raise SetupError(EMPTY_INCLUDE)
    refs = [Ref.parse(r) for r in cfg.include]
    out = Resolved()
    with session(client) as http:
        for alias in dict.fromkeys(r.alias for r in refs):
            index_for(cfg, alias, http, out)
            if (location := out.locations[alias]).startswith("http://"):
                log.warning(f"registry {alias!r} is fetched over plain http, unencrypted: {redact(location)}")
        expanded: dict[str, tuple[list[str], int]] = {}
        for ref in refs:
            via = "direct" if ref.name in out.indexes[ref.alias].matchers else str(ref)
            for key in _expand(ref, (), out, expanded, http)[0]:
                out.matchers.setdefault(key, via)
    return out


def _expand(
    ref: Ref,
    stack: tuple[str, ...],
    out: Resolved,
    expanded: dict[str, tuple[list[str], int]],
    http: httpx.Client,
) -> tuple[list[str], int]:
    """The matchers one reference selects, recursing through bundles, each bundle walked once.

    Memoized per bundle: in a diamond (two bundles sharing a third) the paths to the
    shared bundle multiply with depth, so walking each one is exponential.

    Args:
        ref: The matcher or bundle to expand.
        stack: The bundles currently being expanded, outermost first.
        out: The registries read so far; its indexes and locations are filled in.
        expanded: Bundles already walked, by `alias/name`: their matchers and depth.
        http: The run's HTTP client.

    Returns:
        Its `alias/name` matchers in include order, deduplicated, and how deep its
        bundles nest (0 for a matcher).

    Raises:
        SetupError: On an unknown name, a cycle, nesting over the limit, or a
            bundle file that fails to fetch or validate.
    """
    index, key = out.indexes[ref.alias], str(ref)
    if ref.name in index.matchers:
        return [key], 0
    if ref.name not in index.bundles:
        raise _not_served(ref, index)
    if key in stack:
        raise SetupError(f"bundle cycle: {' -> '.join((*stack, key))}")
    if key not in expanded:
        if len(stack) >= MAX_BUNDLE_DEPTH:
            raise SetupError(f"bundle {stack[0]} nests deeper than {MAX_BUNDLE_DEPTH}")
        bundle = bundle_from(*_fetch_entry(ref, out, http))
        members: dict[str, None] = {}
        depth = 0
        for name in bundle.include:
            sub, sub_depth = _expand(Ref(ref.alias, name), (*stack, key), out, expanded, http)
            members.update(dict.fromkeys(sub))
            depth = max(depth, sub_depth)
        expanded[key] = (list(members), depth + 1)
    if len(stack) + expanded[key][1] > MAX_BUNDLE_DEPTH:  # walked first from a shallower place
        raise SetupError(f"bundle {(*stack, key)[0]} nests deeper than {MAX_BUNDLE_DEPTH}")
    return expanded[key]


def load(cfg: MatchersConfig, client: httpx.Client | None = None) -> list[MatcherConfig]:
    """Resolve, fetch and validate every selected matcher, ids renamed to `alias/id`.

    Args:
        cfg: The `matchers:` config block.
        client: The run's HTTP client, or None for a fresh one.

    Returns:
        The matchers, in include order, ready for `build_bundle`.

    Raises:
        SetupError: On anything :func:`resolve` rejects, or a matcher file that
            can't be fetched or fails a load-time rule.
    """
    with session(client) as http:
        resolved = resolve(cfg, http)
        # model_copy skips validation: the bare id was checked on fetch, and `alias/id`
        # is what makes the same bare id from two registries two distinct matchers.
        return [
            fetch_matcher(Ref.parse(key), resolved, http).model_copy(update={"id": key})
            for key in resolved.matchers
        ]
