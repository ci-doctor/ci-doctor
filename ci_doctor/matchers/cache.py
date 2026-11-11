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
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.matchers.models import INDEX
from ci_doctor.matchers.transports import Validators, redact

log = logging.getLogger("ci_doctor.matchers")

#: The Cache Directory Tagging Specification's first line (https://bford.info/cachedir/).
CACHEDIR_TAG = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file marks ci-doctor's registry cache. Backup and archive tools may skip it.\n"
    "# See https://bford.info/cachedir/\n"
)


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
    """

    body: bytes
    validators: Validators
    fresh_until: float


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
        root = Path(cfg.cache_dir).expanduser() if cfg.cache_dir else default_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
            tag = root / "CACHEDIR.TAG"
            if not tag.is_file():
                _write(tag, CACHEDIR_TAG.encode())
        except OSError as err:
            log.warning(f"registry cache off for this run: {root}: {err.strerror or err}")
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
        folder = self._registry_dir(location)
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            body = (folder / INDEX).read_bytes()
            validators = Validators(etag=meta.get("etag"), last_modified=meta.get("last_modified"))
            return CachedIndex(body, validators, float(meta["fresh_until"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None

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
            _write(path, body)
        except OSError as err:
            with self._lock:
                if not self._warned:
                    self._warned = True
                    log.warning(f"registry cache: cannot write under {self.root}: {err.strerror or err}")


def _write(path: Path, body: bytes) -> None:
    """Replace `path` in one rename, so a parallel reader never sees half a file.

    Raises:
        OSError: If it can't be written.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
