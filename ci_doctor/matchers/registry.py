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
import time
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError

from ci_doctor.config.schemas.matchers import MatchersConfig, unqualified
from ci_doctor.engine import SCHEMAS, Output, Spec, compile_plan
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import INDEX, MAX_BUNDLE_DEPTH, Bundle, Index, Ref
from ci_doctor.matchers.transports import redact, transport_for
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
    return [o.output for o in compile_plan([fetch_matcher(ref, seen, http)]).outputs]


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
    known = [o.output.id for o in compile_plan(specs).outputs]
    problems = []
    for ref in exclude:
        if ref not in known:
            hint = difflib.get_close_matches(ref, known, n=1)
            problems.append(f"{ref}" + (f" (did you mean {hint[0]}?)" if hint else ""))
    if problems:
        raise SetupError("matchers.exclude names no output of an included matcher: " + ", ".join(problems))
