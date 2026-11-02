"""A registry from both sides: building its `index.yaml`, and loading matchers from it.

**Building.** The index is generated, committed, and checked in the registry's CI
(`--check`), so it never ships stale. Generation is deterministic — sorted
entries, one block each — so two PRs touching different matchers merge cleanly,
and a conflict is fixed by rerunning the command.

**Loading.** Every `analyze` resolves `matchers.include` against the registries'
indexes and fetches what it selected: no cache, so a registry is always read as
it is now. What comes back is someone else's file, so every fetch is bounded
(scheme, redirects, size, path containment) and every file is validated again
(`validate.py`). Anything wrong is a `SetupError`: the job exits 1 before
analysis starts, naming the registry and the cause.
"""

import contextlib
import difflib
import hashlib
import logging
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx
import yaml
from pydantic import ValidationError

from ci_doctor.config.schema import MatcherConfig, MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers import transports
from ci_doctor.matchers.models import Bundle, Index, Ref
from ci_doctor.matchers.validate import (
    bundle_from,
    contained,
    load_bundle,
    load_matcher,
    matcher_from,
    parse_yaml,
)

log = logging.getLogger("ci_doctor.matchers")

INDEX = "index.yaml"
#: How deep bundles may nest (a bundle of matchers is depth 1).
MAX_BUNDLE_DEPTH = 5


class _IndexDumper(yaml.SafeDumper):
    """Block mappings, one entry per block; tag lists inline (`[python, test]`)."""


_IndexDumper.add_representer(
    list, lambda dumper, data: dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
)


def _entry(root: Path, path: Path, description: str, classification: list[str] | None = None) -> dict:
    """One index entry for a file.

    Args:
        root: The registry directory.
        path: The file.
        description: Its description.
        classification: Its tags, for a matcher.

    Returns:
        The entry, in index key order.
    """
    entry = {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "description": description,
    }
    if classification is not None:
        entry["classification"] = classification
    return entry


def _bundle_problems(bundles: dict[str, Bundle], matchers: set[str]) -> list[str]:
    """Unknown names, cycles and over-deep nesting across a registry's bundles.

    Args:
        bundles: Every bundle by name.
        matchers: Every matcher name.

    Returns:
        One message per problem.
    """
    problems = []
    for b in bundles.values():
        if unknown := sorted(n for n in b.include if n not in bundles and n not in matchers):
            problems.append(f"bundle {b.id!r} includes unknown names {unknown}")

    depth: dict[str, int] = {}

    def walk(name: str, stack: tuple[str, ...]) -> int:
        if name in stack:
            raise SetupError(f"bundle cycle: {' -> '.join((*stack, name))}")
        if name not in depth:
            subs = [n for n in bundles[name].include if n in bundles]
            depth[name] = 1 + max((walk(n, (*stack, name)) for n in subs), default=0)
        return depth[name]

    for name in sorted(bundles):
        try:
            if walk(name, ()) > MAX_BUNDLE_DEPTH:
                problems.append(f"bundle {name!r} nests {depth[name]} deep, over {MAX_BUNDLE_DEPTH}")
        except SetupError as err:
            problems.append(str(err))
            break
    return problems


def build_index(root: Path) -> str:
    """Validate every matcher and bundle in a registry and render its `index.yaml`.

    Args:
        root: The registry directory.

    Returns:
        The index text. The same tree always gives the same bytes.

    Raises:
        SetupError: Listing every file or cross-file rule that fails.
    """
    if not root.is_dir():
        raise SetupError(f"{root} is not a directory")
    problems: list[str] = []
    matchers: dict[str, dict] = {}
    bundles: dict[str, Bundle] = {}
    bundle_entries: dict[str, dict] = {}

    for path in sorted(root.glob("matchers/*/*/matcher.yaml")):
        try:
            m = load_matcher(path, author=True)
        except SetupError as err:
            problems.append(str(err))
            continue
        if m.id in matchers:
            problems.append(f"{path}: matcher name {m.id!r} is used twice")
        matchers[m.id] = _entry(root, path, m.description or "", list(m.classification))

    for path in sorted(root.glob("bundles/*.yaml")):
        try:
            b = load_bundle(path, author=True)
        except SetupError as err:
            problems.append(str(err))
            continue
        if b.id in matchers:
            problems.append(f"{path}: name {b.id!r} is already a matcher")
        bundles[b.id] = b
        bundle_entries[b.id] = _entry(root, path, b.description)

    problems += _bundle_problems(bundles, set(matchers))
    if problems:
        raise SetupError("registry is invalid:\n  " + "\n  ".join(problems))

    doc = {
        "schema": 1,
        "matchers": dict(sorted(matchers.items())),
        "bundles": dict(sorted(bundle_entries.items())),
    }
    Index.model_validate(doc)  # what we write, ci-doctor must be able to read back
    return yaml.dump(doc, Dumper=_IndexDumper, sort_keys=False, default_flow_style=False, width=4096)


def write_index(root: Path, *, check: bool = False) -> int:
    """Write `index.yaml`, or with `check` verify the committed one is current.

    Args:
        root: The registry directory.
        check: Compare instead of writing.

    Returns:
        The number of entries in the index.

    Raises:
        SetupError: If the registry is invalid, or `check` finds the index stale.
    """
    text = build_index(root)
    target = root / INDEX
    if check:
        if not target.is_file() or target.read_text() != text:
            raise SetupError(f"{target} is out of date. Run `ci-doctor matchers index {root}`")
    else:
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".index.", suffix=".yaml")
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, target)
    doc = yaml.safe_load(text)
    return len(doc["matchers"]) + len(doc["bundles"])


# --- loading ---------------------------------------------------------------------

TIMEOUT = 10.0
MAX_REDIRECTS = 3
INDEX_CAP = 1024 * 1024
FILE_CAP = 64 * 1024
EMPTY_INCLUDE = (
    "no matchers or bundles selected. Run `ci-doctor matchers add official/<name>` "
    "or browse https://matchers.cidoc.dev"
)
_USERINFO = re.compile(r"(?<=://)[^/@\s]+@")


def redact(text: str) -> str:
    """Hide credentials embedded in a URL (`https://user:token@host`).

    Args:
        text: A message that may quote a registry location.

    Returns:
        The message with every URL's userinfo replaced by `***`.
    """
    return _USERINFO.sub("***@", text)


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
        An `http(s)://` or `s3://` URL ending in `/`, a `git+` location as written, or a
        local directory path.

    Raises:
        SetupError: On a location naming the index file, or an unsupported scheme.
    """
    if value.rstrip("/").endswith(".yaml"):
        raise _fail(alias, value, "point at the registry folder, not the index file")
    scheme = urlsplit(value).scheme
    if scheme in ("http", "https"):
        return value if value.endswith("/") else value + "/"
    if transports.is_git(value) or transports.SCP_STYLE.match(value):
        try:
            transports.check_git(value)
        except SetupError as err:
            raise _fail(alias, value, err) from None
        return value
    if scheme == "s3":
        if not urlsplit(value).netloc:
            raise _fail(alias, value, "s3:// needs a bucket: s3://bucket/prefix/")
        return value if value.endswith("/") else value + "/"
    if scheme == "file":
        return unquote(urlsplit(value).path)
    if len(scheme) > 1:  # one letter is a Windows drive, not a scheme
        raise _fail(
            alias,
            value,
            f"unsupported scheme {scheme!r}: use a path, file://, http(s)://, git+https://, git+ssh:// or s3://",
        )
    return value


def fetch_file(alias: str, location: str, rel: str, *, cap: int, client: httpx.Client) -> str:
    """Read one file of a registry. The single place a transport is chosen.

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
    if location.startswith(("http://", "https://")):
        return _http_get(alias, location, rel, cap, client)
    if location.startswith("s3://"):
        try:
            return _decode(alias, location, rel, transports.s3_get(location, rel, cap))
        except SetupError as err:
            raise _fail(alias, location, err) from None
    if transports.is_git(location):
        try:
            root = transports.git_checkout(location)
        except SetupError as err:
            raise _fail(alias, location, err) from None
        return _read_local(alias, str(root), rel, cap, shown=location)
    return _read_local(alias, location, rel, cap)


def _decode(alias: str, location: str, rel: str, body: bytes) -> str:
    """UTF-8 text, or a `SetupError` naming the file.

    Args:
        alias: The registry's alias.
        location: Its directory.
        rel: The file.
        body: Its bytes.

    Returns:
        The decoded text.

    Raises:
        SetupError: If the bytes are not UTF-8.
    """
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise _fail(alias, location, f"{rel} is not UTF-8 text") from None


def _read_local(alias: str, location: str, rel: str, cap: int, shown: str = "") -> str:
    """Read a file from a registry on disk, contained by its real path.

    Args:
        alias: The registry's alias.
        location: Its directory.
        rel: The file, relative to it.
        cap: The most bytes accepted.
        shown: The location to name in errors when `location` is a temporary checkout.

    Returns:
        The file's text.

    Raises:
        SetupError: If it can't be read, is over `cap`, or resolves outside the registry.
    """
    try:
        path = contained(Path(location), rel)
    except SetupError as err:
        raise _fail(alias, shown or location, err) from None
    try:
        size = path.stat().st_size
        body = path.read_bytes() if size <= cap else b""
    except OSError as err:
        raise _fail(alias, shown or location, f"{rel}: {err.strerror or err}") from None
    if size > cap:
        raise _fail(alias, shown or location, f"{rel} is {size} bytes, over the {cap}-byte cap")
    return _decode(alias, shown or location, rel, body)


def _http_get(alias: str, location: str, rel: str, cap: int, client: httpx.Client) -> str:
    """GET a registry file: bounded redirects that never downgrade (another host warns), capped size.

    Args:
        alias: The registry's alias.
        location: Its directory URL, ending in `/`.
        rel: The file, relative to it.
        cap: The most bytes accepted, checked while streaming.
        client: The run's shared HTTP client.

    Returns:
        The file's text.

    Raises:
        SetupError: On a refused redirect, a non-200 answer, an oversized body,
            or a transport error.
    """
    url = urljoin(location, rel)
    if not url.startswith(location):
        raise _fail(alias, location, f"{rel} resolves outside the registry")
    host = urlsplit(location).hostname
    for _ in range(MAX_REDIRECTS + 1):
        try:
            with client.stream("GET", url, follow_redirects=False) as resp:
                if resp.is_redirect:
                    nxt = urljoin(url, resp.headers.get("location", ""))
                    if urlsplit(url).scheme == "https" and urlsplit(nxt).scheme != "https":
                        raise _fail(alias, location, f"{rel}: redirect from https to {nxt} refused")
                    if urlsplit(nxt).hostname != host:
                        log.warning("registry %r: %s redirects to another host (%s)", alias, rel, redact(nxt))
                    url = nxt
                    continue
                if resp.status_code != 200:
                    raise _fail(alias, location, f"GET {rel}: HTTP {resp.status_code}")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > cap:
                        raise _fail(alias, location, f"{rel} is over the {cap}-byte cap")
                return _decode(alias, location, rel, bytes(body))
        except httpx.HTTPError as err:
            raise _fail(alias, location, f"GET {rel}: {err or type(err).__name__}") from None
    raise _fail(alias, location, f"{rel}: more than {MAX_REDIRECTS} redirects")


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
                log.warning(
                    "registry %r is fetched over plain http, unencrypted: %s", alias, redact(location)
                )
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
