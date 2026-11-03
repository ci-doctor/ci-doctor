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
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from ci_doctor.config.schema import MatcherConfig, MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import INDEX, MAX_BUNDLE_DEPTH, Bundle, Index, Ref
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
    """What `matchers.include` expands to, before any matcher file is fetched.

    Attributes:
        locations: Each referenced registry's directory, by alias.
        indexes: Each referenced registry's index, by alias.
        matchers: Every selected `alias/name`, in include order, mapped to how it
            got in: `"direct"`, or the top-level bundle that pulled it in.
    """

    locations: dict[str, str] = field(default_factory=dict)
    indexes: dict[str, Index] = field(default_factory=dict)
    matchers: dict[str, str] = field(default_factory=dict)


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
            if alias not in cfg.registries:
                known = ", ".join(sorted(cfg.registries)) or "none"
                raise SetupError(f"unknown registry {alias!r} in matchers.include (configured: {known})")
            location = registry_dir(alias, cfg.registries[alias])
            if location.startswith("http://"):
                log.warning(f"registry {alias!r} is fetched over plain http, unencrypted: {redact(location)}")
            out.locations[alias] = location
            out.indexes[alias] = fetch_index(alias, location, http)
        bundles: dict[str, Bundle] = {}
        for ref in refs:
            _expand(ref, "direct", (), out, bundles, http)
    return out


def _expand(
    ref: Ref, via: str, stack: tuple[str, ...], out: Resolved, bundles: dict[str, Bundle], http: httpx.Client
) -> None:
    """Add one reference to `out.matchers`, recursing through bundles.

    Args:
        ref: The matcher or bundle to add.
        via: `"direct"`, or the top-level bundle being expanded.
        stack: The bundles currently being expanded, outermost first.
        out: The expansion so far; its indexes and locations are filled in.
        bundles: Bundle files already fetched, by `alias/name`.
        http: The run's HTTP client.

    Raises:
        SetupError: On an unknown name, a cycle, nesting over the limit, or a
            bundle file that fails to fetch or validate.
    """
    index, key = out.indexes[ref.alias], str(ref)
    if ref.name in index.matchers:
        out.matchers.setdefault(key, via)
        return
    if ref.name not in index.bundles:
        hint = difflib.get_close_matches(ref.name, [*index.matchers, *index.bundles], n=1)
        suggestion = f"; did you mean {ref.alias}/{hint[0]}?" if hint else ""
        raise SetupError(f"{key} is not in registry {ref.alias!r}{suggestion}")
    if key in stack:
        raise SetupError(f"bundle cycle: {' -> '.join((*stack, key))}")
    if len(stack) >= MAX_BUNDLE_DEPTH:
        raise SetupError(f"bundle {stack[0]} nests deeper than {MAX_BUNDLE_DEPTH}")
    if key not in bundles:
        location, path = out.locations[ref.alias], index.bundles[ref.name].path
        where = redact(f"registry {ref.alias!r} ({location}): {path}")
        text = fetch_file(ref.alias, location, path, cap=FILE_CAP, client=http)
        bundles[key] = bundle_from(parse_yaml(text, where), where)
    for name in bundles[key].include:
        _expand(Ref(ref.alias, name), key if via == "direct" else via, (*stack, key), out, bundles, http)


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
    matchers = []
    with session(client) as http:
        resolved = resolve(cfg, http)
        for key in resolved.matchers:
            ref = Ref.parse(key)
            location = resolved.locations[ref.alias]
            path = resolved.indexes[ref.alias].matchers[ref.name].path
            where = redact(f"registry {ref.alias!r} ({location}): {path}")
            text = fetch_file(ref.alias, location, path, cap=FILE_CAP, client=http)
            m = matcher_from(parse_yaml(text, where), where)
            if m.id != ref.name:
                raise SetupError(f"{where}: id {m.id!r} does not match its index name {ref.name!r}")
            # model_copy skips validation: the bare id was checked above, and `alias/id`
            # is what makes the same bare id from two registries two distinct matchers.
            matchers.append(m.model_copy(update={"id": key}))
    return matchers
