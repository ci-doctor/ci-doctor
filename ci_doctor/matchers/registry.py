"""The user's side of a registry: resolving `matchers.include` and loading what it selects.

Every `analyze` resolves `matchers.include` against the registries' indexes and
fetches what it selected. An http(s) registry goes through the cache (`cache.py`):
its index is revalidated, and only files whose hash changed are fetched.
What comes back is someone else's file, so every fetch is bounded (scheme,
redirects, size, path containment, in `transports/`), every matcher and bundle
must match the sha256 its index lists, and every file is validated again
(`validate.py`). Anything wrong is a `SetupError`: the job exits 1 before
analysis starts, naming the registry and the cause.

The author's side, building `index.yaml`, is `index.py`.
"""

import contextlib
import difflib
import hashlib
import logging
import time
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from ci_doctor.config.schemas.matchers import MatchersConfig, unqualified
from ci_doctor.engine import SCHEMAS, Output, Spec
from ci_doctor.errors import SetupError
from ci_doctor.matchers import signing
from ci_doctor.matchers.cache import Cache, ago
from ci_doctor.matchers.models import INDEX, MAX_BUNDLE_DEPTH, Bundle, Index, Ref
from ci_doctor.matchers.signing import SIGNATURE, SIGNATURE_CAP
from ci_doctor.matchers.transports import Unreachable, decode, redact, transport_for
from ci_doctor.matchers.validate import bundle_from, matcher_from, parse_yaml

log = logging.getLogger("ci_doctor.matchers")

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
        or a local directory path.

    Raises:
        SetupError: On a location naming the index file, or one no transport accepts.
    """
    if value.rstrip("/").endswith(".yaml"):
        raise _fail(alias, value, "point at the registry folder, not the index file")
    try:
        return transport_for(value).locate(value)
    except SetupError as err:
        raise _fail(alias, value, err) from None


def fetch_file(
    alias: str,
    location: str,
    rel: str,
    *,
    cap: int,
    client: httpx.Client,
    sha256: str | None = None,
    cache: Cache | None = None,
) -> str:
    """Read one file of a registry through the transport its location calls for.

    Args:
        alias: The registry's alias, for errors.
        location: Its directory, from :func:`registry_dir`.
        rel: The file, relative to that directory (already path-checked by `Index`).
        cap: The most bytes accepted.
        client: The run's shared HTTP client.
        sha256: The hash the index lists for this file, checked against its bytes.
        cache: Where a hashed file is looked up first and stored after; None to skip.

    Returns:
        The file's text.

    Raises:
        SetupError: If it can't be read, is over `cap`, leaves the registry, does
            not match `sha256`, or is not UTF-8.
    """
    try:
        transport = transport_for(location)
        store = cache if sha256 is not None and transport.cacheable else None
        if store is not None and sha256 is not None and (hit := store.blob(sha256)) is not None:
            return decode(rel, hit)
        body = transport.read(alias, location, rel, cap=cap, client=client).body or b""
        if sha256 is not None and (actual := hashlib.sha256(body).hexdigest()) != sha256:
            raise SetupError(f"{rel} has sha256 {actual}, but the index lists {sha256}")
        if store is not None and sha256 is not None:
            store.put_blob(sha256, body)
        return decode(rel, body)
    except SetupError as err:
        raise _fail(alias, location, err) from None


def _conditional_body(
    alias: str,
    location: str,
    rel: str,
    *,
    cap: int,
    client: httpx.Client,
    cache: Cache | None,
    use_fresh: bool = True,
) -> tuple[bytes, bool]:
    """A registry file fetched by path: cached while fresh, revalidated after, fetched when changed.

    Args:
        alias: The registry's alias.
        location: Its directory.
        rel: The file (`index.yaml`, `index.yaml.sig`).
        cap: The most bytes accepted.
        client: The run's shared HTTP client.
        cache: The run's cache, or None.
        use_fresh: Whether a cached copy still within its `max-age` is used without asking.

    Returns:
        The file's bytes, and whether they came from the cache without the server's word.

    Raises:
        SetupError: If it can't be fetched, and an outage has no cached copy to fall back on.
    """
    transport = transport_for(location)
    store = cache if transport.cacheable else None
    cached = store.file(location, rel) if store is not None else None
    if use_fresh and cached is not None and time.time() < cached.fresh_until:
        return cached.body, True
    since = cached.validators if cached is not None else None
    try:
        fetched = transport.read(alias, location, rel, cap=cap, client=client, since=since)
    except Unreachable as err:
        if cached is None:
            raise
        what = "index" if rel == INDEX else rel
        log.warning(
            f"registry {alias!r} is unreachable ({err}); using its {what} cached {ago(time.time() - cached.fetched_at)} ago"
        )
        return cached.body, True
    if fetched.body is None and cached is not None and store is not None:
        store.revalidated(location, rel, fetched.validators, fetched.max_age)
        return cached.body, False
    body = fetched.body or b""
    if store is not None:
        store.put_file(location, rel, body, fetched.validators, fetched.max_age)
    return body, False


def _verified_signature(
    alias: str, location: str, index: bytes, client: httpx.Client, cache: Cache | None, *, use_fresh: bool
) -> bytes:
    """The official registry's `index.yaml.sig`, checked against `index`, and newer than any seen.

    Args:
        alias: The registry's alias.
        location: Its directory.
        index: The index's bytes, as fetched.
        client: The run's shared HTTP client.
        cache: The run's cache, which also holds the last version seen; None skips the rollback check.
        use_fresh: Whether a fresh cached signature may be used: only when the index itself was.

    Returns:
        The signature's bytes.

    Raises:
        SetupError: If it is missing, doesn't verify, has expired, or is older than one already seen.
    """
    sig, _ = _conditional_body(
        alias, location, SIGNATURE, cap=SIGNATURE_CAP, client=client, cache=cache, use_fresh=use_fresh
    )
    signed = signing.verify(index, sig, signing.OFFICIAL_KEYS, datetime.now(UTC))
    last = cache.signed_version(location) if cache is not None else None
    if last is not None and signed.version < last:
        raise SetupError(
            f"the signed index is version {signed.version}, older than version {last} already seen: refused as a rollback"
        )
    if cache is not None and (last is None or signed.version > last):
        cache.record_signed_version(location, signed.version)
    return sig


def index_text(
    alias: str, location: str, client: httpx.Client, cache: Cache | None = None
) -> tuple[str, bytes | None]:
    """A registry's `index.yaml` as published, through the cache; the official one signature-checked.

    Args:
        alias: The registry's alias; `official` must carry a valid signature.
        location: Its directory, from :func:`registry_dir`.
        client: The run's shared HTTP client.
        cache: The run's cache, or None to always fetch.

    Returns:
        The index's text, unparsed, and for `official` its verified `index.yaml.sig` (else None).

    Raises:
        SetupError: If it can't be fetched, isn't UTF-8, or (for `official`) its signature fails.
    """
    try:
        body, fresh = _conditional_body(alias, location, INDEX, cap=INDEX_CAP, client=client, cache=cache)
        sig = (
            _verified_signature(alias, location, body, client, cache, use_fresh=fresh)
            if alias == signing.OFFICIAL
            else None
        )
        return decode(INDEX, body), sig
    except SetupError as err:
        raise _fail(alias, location, err) from None


def parse_index(alias: str, location: str, text: str) -> Index:
    """Parse and validate an index's text.

    Args:
        alias: The registry's alias, for errors.
        location: Its directory, for errors.
        text: From :func:`index_text`.

    Returns:
        The validated index: entry count, names and path rules already checked.

    Raises:
        SetupError: If it isn't valid YAML or doesn't validate.
    """
    data = parse_yaml(text, redact(f"registry {alias!r} ({location}): {INDEX}"))
    try:
        return Index.model_validate(data)
    except ValidationError as err:
        raise _fail(alias, location, f"{INDEX}: {err}") from None


def fetch_index(alias: str, location: str, client: httpx.Client, cache: Cache | None = None) -> Index:
    """Fetch and validate a registry's `index.yaml`.

    Args:
        alias: The registry's alias.
        location: Its directory, from :func:`registry_dir`.
        client: The run's shared HTTP client.
        cache: The run's cache, or None to always fetch.

    Returns:
        The validated index.

    Raises:
        SetupError: If it can't be fetched, parsed or validated.
    """
    return parse_index(alias, location, index_text(alias, location, client, cache)[0])


@contextlib.contextmanager
def session(cfg: MatchersConfig, client: httpx.Client | None = None) -> Iterator[httpx.Client]:
    """The caller's HTTP client, or a fresh one closed afterwards.

    Args:
        cfg: The `matchers:` block, for its timeout and how many fetches run at once.
        client: A client to reuse (tests pass one over `httpx.MockTransport`).

    Yields:
        The client.
    """
    if client is not None:
        yield client
        return
    limits = httpx.Limits(max_connections=max(cfg.fetch_workers, 10))
    with httpx.Client(timeout=cfg.timeout_seconds, limits=limits) as fresh:
        yield fresh


@dataclass
class Resolved:
    """Registries read so far, and what `matchers.include` expands to.

    Attributes:
        locations: Each referenced registry's directory, by alias.
        indexes: Each referenced registry's index, by alias.
        matchers: Every selected `alias/name`, in include order, mapped to how it
            got in: `"direct"`, or the top-level bundle that pulled it in.
        cache: Where registry files are cached, or None to always fetch.
    """

    locations: dict[str, str] = field(default_factory=dict)
    indexes: dict[str, Index] = field(default_factory=dict)
    matchers: dict[str, str] = field(default_factory=dict)
    cache: Cache | None = None


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
        seen.indexes[alias] = fetch_index(alias, seen.locations[alias], http, seen.cache)
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
        SetupError: If the file can't be fetched, doesn't match its index hash, or isn't valid YAML.
    """
    index, location = seen.indexes[ref.alias], seen.locations[ref.alias]
    entry = index.matchers.get(ref.name) or index.bundles[ref.name]
    where = redact(f"registry {ref.alias!r} ({location}): {entry.path}")
    text = fetch_file(
        ref.alias, location, entry.path, cap=FILE_CAP, client=http, sha256=entry.sha256, cache=seen.cache
    )
    return parse_yaml(text, where), where


def fetch_matcher(ref: Ref, seen: Resolved, http: httpx.Client) -> Spec:
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


def fetch_outputs(ref: Ref, seen: Resolved, http: httpx.Client) -> list[Output]:
    """What one served matcher puts in the prompt: each output's id, role and description.

    Args:
        ref: A matcher its registry's index lists.
        seen: Registries already read, holding `ref`'s.
        http: The run's HTTP client.

    Returns:
        Its outputs, in file order.

    Raises:
        SetupError: If the matcher can't be fetched or fails a load-time rule.
    """
    m = fetch_matcher(ref, seen, http)
    return [Output(output_id, o.role, o.description) for output_id, _, o in m.named_outputs()]


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
    out = Resolved(cache=Cache.open(cfg))
    with session(cfg, client) as http:
        for alias in dict.fromkeys(r.alias for r in refs):
            index_for(cfg, alias, http, out)
            if (location := out.locations[alias]).startswith("http://"):
                log.warning(f"registry {alias!r} is fetched over plain http, unencrypted: {redact(location)}")
        bundles = _prefetch_bundles(refs, out, http, cfg.fetch_workers)
        walked: dict[str, tuple[list[str], int]] = {}
        for ref in refs:
            keys, depth = _expand(ref, (), out, walked, bundles, http)
            if depth > MAX_BUNDLE_DEPTH:
                raise SetupError(f"bundle {ref} nests deeper than {MAX_BUNDLE_DEPTH}")
            via = "direct" if depth == 0 else str(ref)
            for key in keys:
                out.matchers.setdefault(key, via)
    _drop_unreadable(out)
    return out


def _drop_unreadable(out: Resolved) -> None:
    """Skip matchers in a schema version this ci-doctor doesn't know, judged from the index.

    A bundle is someone else's list, so a newer matcher in it is skipped with a
    warning and the rest still load. A matcher the user named directly is one they
    asked for, so that fails setup instead (invariant #3: before analysis starts).

    Args:
        out: The expansion so far; its matchers are narrowed to the readable ones.

    Raises:
        SetupError: For an unreadable matcher included by name.
    """
    readable = {}
    for key, via in out.matchers.items():
        ref = Ref.parse(key)
        version = out.indexes[ref.alias].matchers[ref.name].schema_version
        if version is None or version in SCHEMAS:
            readable[key] = via
            continue
        message = f"{key} needs a newer ci-doctor (schema {version})"
        if via == "direct":
            raise SetupError(message)
        log.warning(f"{message}; skipped from {via}")
    out.matchers = readable


def _prefetch_bundles(refs: list[Ref], out: Resolved, http: httpx.Client, workers: int) -> dict[str, Bundle]:
    """Fetch every bundle `refs` reach, a nesting level at a time, in parallel.

    One round trip per level instead of per bundle. Names, cycles and depth are
    `_expand`'s to report; a bundle too deep to prefetch it fetches itself.

    Args:
        refs: The top-level `matchers.include` entries.
        out: The registries read so far, indexes included.
        http: The run's HTTP client.
        workers: How many files to fetch at once.

    Returns:
        Each bundle reached, by `alias/name`.

    Raises:
        SetupError: If a bundle file fails to fetch or validate.
    """
    fetched: dict[str, Bundle] = {}
    level = refs
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ci-doctor-fetch") as pool:
        for _ in range(MAX_BUNDLE_DEPTH):
            todo = [
                r
                for r in dict.fromkeys(level)
                if r.name in out.indexes[r.alias].bundles and str(r) not in fetched
            ]
            if not todo:
                break
            fetched |= zip(
                map(str, todo),
                pool.map(lambda r: bundle_from(*_fetch_entry(r, out, http)), todo),
                strict=True,
            )
            level = [Ref(r.alias, name) for r in todo for name in fetched[str(r)].include]
    return fetched


def _expand(
    ref: Ref,
    stack: tuple[str, ...],
    out: Resolved,
    walked: dict[str, tuple[list[str], int]],
    bundles: dict[str, Bundle],
    http: httpx.Client,
) -> tuple[list[str], int]:
    """The `alias/name` matchers `ref` selects, and how deep its bundles nest.

    Each bundle is walked once (`walked`), so diamonds don't multiply; a memoized
    bundle reports its depth and `resolve()` checks the total.

    Args:
        ref: The matcher or bundle to expand.
        stack: The bundles currently being expanded, outermost first.
        out: The registries read so far.
        walked: Bundles already expanded, by `alias/name`: their matchers and depth.
        bundles: Bundle files already fetched, by `alias/name`.
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
    if len(stack) >= MAX_BUNDLE_DEPTH:
        raise SetupError(f"bundle {stack[0]} nests deeper than {MAX_BUNDLE_DEPTH}")
    if key not in walked:
        bundle = bundles[key] if key in bundles else bundle_from(*_fetch_entry(ref, out, http))
        subs = [
            _expand(Ref(ref.alias, name), (*stack, key), out, walked, bundles, http)
            for name in bundle.include
        ]
        members = dict.fromkeys(k for keys, _ in subs for k in keys)
        walked[key] = (list(members), 1 + max((depth for _, depth in subs), default=0))
    return walked[key]


def via(cfg: MatchersConfig, include: list[str], http: httpx.Client) -> dict[str, str]:
    """Which top-level entry of `include` brings in each matcher: `"direct"` or the bundle.

    Args:
        cfg: The `matchers:` block (its registries and fetch settings).
        include: The refs to expand.
        http: The run's HTTP client.

    Returns:
        `alias/name` -> `"direct"` or the bundle; empty when `include` is.
    """
    if not include:
        return {}
    return resolve(cfg.model_copy(update={"include": include}), http).matchers


def load(cfg: MatchersConfig, client: httpx.Client | None = None) -> list[Spec]:
    """Resolve, fetch and validate every selected matcher, ids renamed to `alias/id`.

    Args:
        cfg: The `matchers:` config block.
        client: The run's HTTP client, or None for a fresh one.

    Returns:
        The matchers, in include order, ready for `build_bundle`.

    Raises:
        SetupError: On anything :func:`resolve` rejects, a matcher file that
            can't be fetched or fails a load-time rule, or a `matchers.exclude`
            entry that names no output of an included matcher.
    """
    start = time.perf_counter()
    with session(cfg, client) as http:
        resolved = resolve(cfg, http)
        keys = list(resolved.matchers)
        # map() keeps include order, and re-raises the first failure in that order.
        with ThreadPoolExecutor(max_workers=cfg.fetch_workers, thread_name_prefix="ci-doctor-fetch") as pool:
            fetched = list(pool.map(lambda key: fetch_matcher(Ref.parse(key), resolved, http), keys))
    log.debug(
        f"loaded {len(keys)} matchers from {len(resolved.indexes)} registries in {time.perf_counter() - start:.1f}s"
    )
    # `alias/id` keeps the same bare id from two registries apart.
    specs = [m.model_copy(update={"id": key}) for key, m in zip(keys, fetched, strict=True)]
    _check_exclude(cfg.exclude, specs)
    return specs


def _check_exclude(exclude: list[str], specs: list[Spec]) -> None:
    """Make every `matchers.exclude` entry name a loaded output, so a typo isn't a silent no-op.

    Args:
        exclude: The configured entries.
        specs: The loaded matchers, ids already `alias/id`.

    Raises:
        SetupError: Naming each entry that matches nothing, with the closest output.
    """
    if not exclude:
        return
    known = [output_id for spec in specs for output_id, _, _ in spec.named_outputs()]
    problems = []
    for ref in exclude:
        if ref not in known:
            hint = difflib.get_close_matches(ref, known, n=1)
            problems.append(f"{ref}" + (f" (did you mean {hint[0]}?)" if hint else ""))
    if problems:
        raise SetupError("matchers.exclude names no output of an included matcher: " + ", ".join(problems))
