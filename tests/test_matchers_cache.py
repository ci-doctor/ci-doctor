"""The registry cache: what a run fetches when it already has the files, and what it keeps.

Offline: the registry is `httpx.MockTransport` serving a `tests.support` registry,
with the `ETag` / `Last-Modified` / `Cache-Control` a static host sends.
"""

import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest

from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.cache import Cache, default_root  # bound before the autouse fixture swaps it
from ci_doctor.matchers.index import write_index
from ci_doctor.matchers.mirror import mirror
from ci_doctor.matchers.registry import load
from tests import support

BASE = "https://reg.test/m/"
LAST_MODIFIED = "Wed, 11 Nov 2026 10:00:00 GMT"


class Server:
    """A static host: every file with validators, answering conditional GETs with `304`."""

    def __init__(
        self, root: Path, *, etag: bool = True, cache_control: str | None = None, age: str | None = None
    ):
        """Serve `root`, with an ETag (else Last-Modified) and optional freshness headers."""
        self.root, self.etag, self.cache_control, self.age = root, etag, cache_control, age
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.down: int | Exception | None = None  # an outage: an HTTP status, or an error to raise

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Answer one request as a static host would."""
        rel = str(request.url).removeprefix(BASE)
        self.requests.append((rel, dict(request.headers)))
        if isinstance(self.down, Exception):
            raise self.down
        if self.down:
            return httpx.Response(self.down)
        path = self.root / rel
        if not path.is_file():
            return httpx.Response(404)
        body = path.read_bytes()
        tag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
        headers = {"etag": tag} if self.etag else {"last-modified": LAST_MODIFIED}
        headers |= {"cache-control": self.cache_control} if self.cache_control else {}
        headers |= {"age": self.age} if self.age else {}
        unchanged = (self.etag and request.headers.get("if-none-match") == tag) or (
            not self.etag and request.headers.get("if-modified-since") == LAST_MODIFIED
        )
        return httpx.Response(304 if unchanged else 200, headers=headers, content=b"" if unchanged else body)

    def client(self) -> httpx.Client:
        """A client whose every request this server answers."""
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def fetched(self) -> list[str]:
        """The files requested since the last call, then forget them."""
        out, self.requests = [rel for rel, _ in self.requests], []
        return out


@pytest.fixture
def registry(tmp_path):
    """A two-matcher registry, free to edit and re-index."""
    return support.py_registry(tmp_path / "reg")


def _cfg(tmp_path, **kw) -> MatchersConfig:
    return MatchersConfig(
        registries={"fx": BASE}, include=["fx/all"], cache_dir=str(tmp_path / "cache"), **kw
    )


def _load(cfg: MatchersConfig, server: Server) -> list[str]:
    return [m.id for m in load(cfg, client=server.client())]


def test_a_warm_run_revalidates_the_index_and_fetches_nothing_else(tmp_path, registry):
    """Unchanged registry: one conditional GET for the index (`304`), every file from the cache."""
    server, cfg = Server(registry), _cfg(tmp_path)
    cold = _load(cfg, server)
    assert len(server.fetched()) == 5  # index, 2 bundles, 2 matchers
    assert _load(cfg, server) == cold
    assert server.requests[0][1]["if-none-match"].startswith('"')
    assert server.fetched() == ["index.yaml"]


def test_a_fresh_index_is_used_without_a_request(tmp_path, registry):
    """Within the server's `max-age` (minus `Age`), a warm run makes no request at all."""
    server, cfg = Server(registry, cache_control="max-age=600", age="30"), _cfg(tmp_path)
    _load(cfg, server)
    server.fetched()
    _load(cfg, server)
    assert server.fetched() == []
    meta = json.loads(next((tmp_path / "cache" / "registries").glob("*/index.yaml.meta.json")).read_text())
    assert 560 <= meta["fresh_until"] - meta["fetched_at"] <= 570  # 600 - 30, measured from now


@pytest.mark.parametrize("control", ["no-cache, max-age=600", None])
def test_an_index_without_freshness_is_revalidated_every_run(tmp_path, registry, control):
    """`no-cache`, or no `max-age` at all, means ask every time."""
    server, cfg = Server(registry, cache_control=control), _cfg(tmp_path)
    _load(cfg, server)
    server.fetched()
    _load(cfg, server)
    assert server.fetched() == ["index.yaml"]


def test_last_modified_is_the_validator_when_there_is_no_etag(tmp_path, registry):
    """`If-Modified-Since` carries the cached `Last-Modified`."""
    server, cfg = Server(registry, etag=False), _cfg(tmp_path)
    _load(cfg, server)
    server.fetched()
    _load(cfg, server)
    assert server.requests[0][1]["if-modified-since"] == LAST_MODIFIED
    assert server.fetched() == ["index.yaml"]


def test_only_the_files_whose_hash_changed_are_fetched(tmp_path, registry):
    """A new index costs one GET per changed file; the rest come from the cache by hash."""
    server, cfg = Server(registry), _cfg(tmp_path)
    _load(cfg, server)
    server.fetched()
    path = registry / "matchers/python/pytest/matcher.yaml"
    path.write_text(path.read_text().replace("short summary.", "short test summary line."))
    write_index(registry)
    _load(cfg, server)
    assert server.fetched() == ["index.yaml", "matchers/python/pytest/matcher.yaml"]


def test_a_damaged_blob_is_fetched_again(tmp_path, registry):
    """A cached file is trusted only while its bytes still hash to its name."""
    server, cfg = Server(registry), _cfg(tmp_path)
    _load(cfg, server)
    server.fetched()
    blob = next(p for p in (tmp_path / "cache" / "blobs").rglob("*") if p.is_file())
    blob.write_bytes(b"damaged")
    _load(cfg, server)
    assert len(server.fetched()) == 2  # the index, and the damaged file
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == blob.name


def test_the_cache_root_is_tagged_for_backup_tools(tmp_path, registry):
    """`CACHEDIR.TAG` starts with the spec's signature line."""
    _load(_cfg(tmp_path), Server(registry))
    tag = (tmp_path / "cache" / "CACHEDIR.TAG").read_text()
    assert tag.startswith("Signature: 8a477f597d28d172789f06886806bc55\n")


def test_cache_off_fetches_everything_and_writes_nothing(tmp_path, registry):
    """`matchers.cache: false`: every run reads the registry as it is now."""
    server, cfg = Server(registry), _cfg(tmp_path, cache=False)
    _load(cfg, server)
    _load(cfg, server)
    assert len(server.fetched()) == 10
    assert not (tmp_path / "cache").exists()


def test_a_local_registry_is_not_cached(tmp_path, registry):
    """Reading a registry on disk is already a disk read; nothing is copied."""
    cfg = MatchersConfig(
        registries={"fx": str(registry)}, include=["fx/all"], cache_dir=str(tmp_path / "cache")
    )
    assert len(load(cfg)) == 2
    assert not (tmp_path / "cache" / "blobs").exists()


def test_an_unusable_cache_dir_warns_and_the_run_goes_on(tmp_path, registry, caplog):
    """The cache is an optimisation: a cache_dir that is a file is reported, never fatal."""
    (tmp_path / "cache").write_text("not a directory")
    server = Server(registry)
    with caplog.at_level(logging.WARNING, logger="ci_doctor.matchers"):
        assert len(_load(_cfg(tmp_path), server)) == 2
    assert "registry cache off for this run" in caplog.text


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("linux", {"XDG_CACHE_HOME": "{tmp}/xdg"}, "{tmp}/xdg/ci-doctor"),
        ("linux", {"XDG_CACHE_HOME": "relative/dir"}, "{tmp}/home/.cache/ci-doctor"),  # the spec ignores it
        ("darwin", {}, "{tmp}/home/Library/Caches/ci-doctor"),
        ("win32", {"LOCALAPPDATA": "{tmp}/local"}, "{tmp}/local/ci-doctor/Cache"),
    ],
)
def test_the_default_location_is_the_platform_cache_dir(monkeypatch, tmp_path, platform, env, expected):
    """Each platform's per-user cache directory, never the config directory."""
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value.format(tmp=tmp_path))
    assert default_root() == Path(expected.format(tmp=tmp_path))


def test_a_cache_off_config_opens_no_cache(tmp_path):
    """`Cache.open` is the one place that decides; off means None, nothing created."""
    assert Cache.open(_cfg(tmp_path, cache=False)) is None
    assert Cache.open(_cfg(tmp_path)) is not None


# --- an outage --------------------------------------------------------------------


@pytest.mark.parametrize("down", [503, httpx.ConnectError("connection refused")], ids=["5xx", "refused"])
def test_an_outage_falls_back_to_the_cached_index_with_a_warning(tmp_path, registry, caplog, down):
    """The registry's outage isn't the setup's: a run with everything cached carries on, and says so."""
    server, cfg = Server(registry), _cfg(tmp_path)
    cold = _load(cfg, server)
    server.down = down
    with caplog.at_level(logging.WARNING, logger="ci_doctor.matchers"):
        assert _load(cfg, server) == cold
    assert "registry 'fx' is unreachable" in caplog.text
    assert "using its index cached 0s ago" in caplog.text


def test_an_outage_with_nothing_cached_fails_setup(tmp_path, registry):
    """No cached index, nothing to fall back on: the setup error it always was."""
    server = Server(registry)
    server.down = 503
    with pytest.raises(SetupError, match="GET index.yaml: HTTP 503"):
        _load(_cfg(tmp_path), server)


def test_a_404_is_not_an_outage(tmp_path, registry):
    """A missing index means the registry moved or is misconfigured; the cache doesn't hide that."""
    server, cfg = Server(registry), _cfg(tmp_path)
    _load(cfg, server)
    server.down = 404
    with pytest.raises(SetupError, match="GET index.yaml: HTTP 404"):
        _load(cfg, server)


def test_an_outage_with_a_file_missing_from_the_cache_fails_setup_naming_it(tmp_path, registry):
    """The fallback covers only what is cached; anything else still has to be fetched."""
    server, cfg = Server(registry), _cfg(tmp_path)
    _load(cfg, server)
    sha = hashlib.sha256((registry / "matchers/python/pytest/matcher.yaml").read_bytes()).hexdigest()
    (tmp_path / "cache" / "blobs" / "sha256" / sha[:2] / sha).unlink()
    server.down = 503
    with pytest.raises(SetupError, match="GET matchers/python/pytest/matcher.yaml: HTTP 503"):
        _load(cfg, server)


# --- prune and clean ----------------------------------------------------------------


def test_prune_keeps_what_a_cached_index_lists_and_drops_the_rest(tmp_path, registry):
    """After a matcher changes, its old version is the only file no index lists."""
    server, cfg = Server(registry), _cfg(tmp_path)
    _load(cfg, server)
    path = registry / "matchers/python/pytest/matcher.yaml"
    old = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text().replace("short summary.", "short test summary line."))
    write_index(registry)
    _load(cfg, server)
    cache = Cache(tmp_path / "cache")
    assert len(cache.blob_paths()) == 5  # 2 bundles, 2 matchers, and the old pytest
    assert [p.name for p in cache.prune()] == [old]
    assert len(cache.blob_paths()) == 4
    server.fetched()
    _load(cfg, server)
    assert server.fetched() == ["index.yaml"]  # nothing the index lists was pruned


def test_clean_empties_everything_but_the_tagged_root(tmp_path, registry):
    """The next run is a cold one."""
    server, cfg = Server(registry), _cfg(tmp_path)
    _load(cfg, server)
    Cache(tmp_path / "cache").clean()
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == ["CACHEDIR.TAG"]
    server.fetched()
    _load(cfg, server)
    assert len(server.fetched()) == 5


def test_a_mirror_with_a_bad_file_writes_nothing(tmp_path, registry):
    """Every file is fetched and hash-checked before the first one is written."""
    path = registry / "bundles/all.yaml"
    path.write_text(path.read_text() + "# altered after indexing\n")
    with pytest.raises(SetupError, match="bundles/all.yaml has sha256"):
        mirror(_cfg(tmp_path), "fx", tmp_path / "copy", client=Server(registry).client())
    assert not (tmp_path / "copy").exists()
