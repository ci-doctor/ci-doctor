"""A content-addressed cache of registry files, so a run fetches only what changed.

Matchers and bundles are stored by the sha256 their index lists (`blobs/sha256/ab/abcd…`):
a file whose hash is unchanged is never fetched again, from any registry, and a
blob is trusted because its name is its hash. Each http(s) registry's `index.yaml`
is kept with its HTTP validators and how long the server said it stays fresh, so
it is reused without a request while fresh and revalidated with a conditional GET
after that. Local registries aren't cached: reading them is already a disk read.

The cache is an optimisation, never a requirement: a directory that can't be
written logs one warning and the run goes on without it.
"""

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.lib.files import atomic_write_bytes
from ci_doctor.matchers.models import INDEX
from ci_doctor.matchers.transports import Validators, redact
from ci_doctor.matchers.validate import parse_yaml

log = logging.getLogger("ci_doctor.matchers")

#: The Cache Directory Tagging Specification's first line (https://bford.info/cachedir/).
CACHEDIR_TAG = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file marks ci-doctor's registry cache. Backup and archive tools may skip it.\n"
    "# See https://bford.info/cachedir/\n"
)


def ago(seconds: float) -> str:
    """A duration as a person reads it: `42s`, `5m`, `3h`, `2d`.

    Args:
        seconds: How long ago.

    Returns:
        The largest whole unit.
    """
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{max(0, int(seconds))}s"


def default_root() -> Path:
    r"""The per-user cache directory each platform expects.

    Returns:
        `%LOCALAPPDATA%\ci-doctor\Cache` on Windows, `~/Library/Caches/ci-doctor`
        on macOS, `$XDG_CACHE_HOME/ci-doctor` (default `~/.cache/ci-doctor`) elsewhere.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ci-doctor" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ci-doctor"
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    return (Path(xdg) if os.path.isabs(xdg) else Path.home() / ".cache") / "ci-doctor"


@dataclass(frozen=True)
class CachedIndex:
    """A registry's index as last fetched.

    Attributes:
        body: The index's bytes.
        validators: What to revalidate it with.
        fresh_until: Unix time until which it is used without a request.
        fetched_at: Unix time it was last fetched or revalidated.
        location: The registry it came from, redacted.
    """

    body: bytes
    validators: Validators
    fresh_until: float
    fetched_at: float = 0.0
    location: str = ""

    def hashes(self) -> set[str]:
        """Every sha256 this index lists, for its matchers and bundles.

        Returns:
            The hashes; none if the cached index no longer parses.
        """
        return _hashes(self.body)


class Cache:
    """One cache directory, shared by every registry a run reads."""

    def __init__(self, root: Path) -> None:
        """Use `root`, which already exists and carries its `CACHEDIR.TAG`.

        Args:
            root: The cache directory.
        """
        self.root = root
        self._warned = False
        self._lock = threading.Lock()

    @classmethod
    def open(cls, cfg: MatchersConfig) -> "Cache | None":
        """The cache `cfg` asks for, created if missing; None when it is off or can't be created.

        Args:
            cfg: The `matchers:` block (`cache`, `cache_dir`).

        Returns:
            The cache, or None.
        """
        if not cfg.cache:
            return None
        root = root_for(cfg)
        try:
            root.mkdir(parents=True, exist_ok=True)
            tag = root / "CACHEDIR.TAG"
            if not tag.is_file():
                atomic_write_bytes(tag, CACHEDIR_TAG.encode())
        except (OSError, SetupError) as err:
            log.warning(f"registry cache off for this run: {root}: {getattr(err, 'strerror', None) or err}")
            return None
        return cls(root)

    def blob(self, sha256: str) -> bytes | None:
        """A cached file by its hash; a damaged one is dropped.

        Args:
            sha256: The hash its index lists.

        Returns:
            Its bytes, or None if it isn't cached (or no longer matches its name).
        """
        path = self._blob_path(sha256)
        try:
            body = path.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(body).hexdigest() != sha256:
            path.unlink(missing_ok=True)
            return None
        return body

    def put_blob(self, sha256: str, body: bytes) -> None:
        """Store a file the caller already checked against `sha256`.

        Args:
            sha256: Its hash.
            body: Its bytes.
        """
        self._store(self._blob_path(sha256), body)

    def index(self, location: str) -> CachedIndex | None:
        """A registry's cached index, if one is cached and readable.

        Args:
            location: The registry's directory URL.

        Returns:
            The cached index, or None.
        """
        return _read_index(self._registry_dir(location))

    def indexes(self) -> list[CachedIndex]:
        """Every registry index in the cache, oldest fetch first.

        Returns:
            The readable ones; a damaged entry is skipped.
        """
        folders = sorted((self.root / "registries").glob("*/")) if (self.root / "registries").is_dir() else []
        found = [i for i in map(_read_index, folders) if i is not None]
        return sorted(found, key=lambda i: i.fetched_at)

    def blob_paths(self) -> list[Path]:
        """Every cached file.

        Returns:
            Their paths, named by their sha256.
        """
        blobs = self.root / "blobs" / "sha256"
        return sorted(p for p in blobs.glob("*/*") if p.is_file()) if blobs.is_dir() else []

    def prune(self) -> list[Path]:
        """Delete every cached file that no cached index lists.

        Returns:
            The files deleted.
        """
        listed = set().union(*(i.hashes() for i in self.indexes()))
        dropped = [p for p in self.blob_paths() if p.name not in listed]
        for path in dropped:
            path.unlink(missing_ok=True)
        return dropped

    def clean(self) -> None:
        """Delete every cached file and index; the tagged root stays."""
        for sub in ("blobs", "registries"):
            shutil.rmtree(self.root / sub, ignore_errors=True)

    def put_index(self, location: str, body: bytes, validators: Validators, max_age: float) -> None:
        """Store a registry's freshly fetched index.

        Args:
            location: The registry's directory URL.
            body: The index's bytes.
            validators: Its response's validators.
            max_age: How many seconds it stays fresh, from its response.
        """
        folder = self._registry_dir(location)
        self._store(folder / INDEX, body)
        self._store_meta(folder, location, validators, max_age)

    def revalidated(self, location: str, validators: Validators, max_age: float) -> None:
        """Record that a cached index is still current (the server answered `304`).

        Args:
            location: The registry's directory URL.
            validators: The validators to keep using.
            max_age: How many seconds it stays fresh, from the `304`.
        """
        self._store_meta(self._registry_dir(location), location, validators, max_age)

    def _store_meta(self, folder: Path, location: str, validators: Validators, max_age: float) -> None:
        now = time.time()
        meta = {
            "location": redact(location),
            "etag": validators.etag,
            "last_modified": validators.last_modified,
            "fetched_at": now,
            "fresh_until": now + max_age,
        }
        self._store(folder / "meta.json", json.dumps(meta, indent=2).encode())

    def _blob_path(self, sha256: str) -> Path:
        return self.root / "blobs" / "sha256" / sha256[:2] / sha256

    def _registry_dir(self, location: str) -> Path:
        # Keyed by location, not alias: an alias can be re-pointed, a location can't.
        return self.root / "registries" / hashlib.sha256(location.encode()).hexdigest()[:32]

    def _store(self, path: Path, body: bytes) -> None:
        """Write a cache file atomically; a failure warns once and is otherwise ignored."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(path, body)
        except (OSError, SetupError) as err:
            with self._lock:
                if not self._warned:
                    self._warned = True
                    log.warning(
                        f"registry cache: cannot write under {self.root}: {getattr(err, 'strerror', None) or err}"
                    )


def root_for(cfg: MatchersConfig) -> Path:
    """Where `cfg` puts the cache, whether or not it exists or is on.

    Args:
        cfg: The `matchers:` block.

    Returns:
        `matchers.cache_dir` (relative to the working directory), else :func:`default_root`.
    """
    return Path(cfg.cache_dir).expanduser() if cfg.cache_dir else default_root()


def _read_index(folder: Path) -> CachedIndex | None:
    """One registry's cached index and its metadata, if both are readable.

    Args:
        folder: Its directory under `registries/`.

    Returns:
        The cached index, or None.
    """
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        body = (folder / INDEX).read_bytes()
        validators = Validators(etag=meta.get("etag"), last_modified=meta.get("last_modified"))
        return CachedIndex(
            body,
            validators,
            float(meta["fresh_until"]),
            float(meta["fetched_at"]),
            str(meta.get("location", "")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _hashes(index_body: bytes) -> set[str]:
    """Every sha256 an index lists, for its matchers and its bundles.

    Args:
        index_body: The index's bytes.

    Returns:
        The hashes; none for an index that doesn't parse.
    """
    try:
        doc = parse_yaml(index_body.decode("utf-8"), INDEX)
    except (SetupError, UnicodeDecodeError):
        return set()
    if not isinstance(doc, dict):
        return set()
    entries = [
        e for kind in ("matchers", "bundles") if isinstance(doc.get(kind), dict) for e in doc[kind].values()
    ]
    return {e["sha256"] for e in entries if isinstance(e, dict) and isinstance(e.get("sha256"), str)}
