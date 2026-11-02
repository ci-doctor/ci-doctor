"""Loading matchers from registries: resolve, fetch, validate, rename.

Every case is offline: local registries are directories under tmp_path or the
committed fixture registry, and `http(s)://` goes through `httpx.MockTransport`.
"""

import logging

import httpx
import pytest
import yaml

from ci_doctor.config.schema import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import FILE_CAP, INDEX_CAP, load, resolve
from tests import support

_MATCHER = {
    "role": "tool",
    "classification": ["python"],
    "description": "A matcher written for the registry loading tests.",
    "pattern": "^boom",
}
_SHA = "0" * 64


def _cfg(include, **registries):
    """A `matchers:` block over the given registries."""
    return MatchersConfig(registries=registries, include=include)


def _registry(root, matchers=(), bundles=None, index=None):
    """Write a registry by hand, so it can break rules `matchers index` would refuse.

    Args:
        root: The registry directory.
        matchers: Matcher names to write (or `{name: mapping}` to control the file).
        bundles: `{name: [include, ...]}`.
        index: A replacement index document, written as-is.

    Returns:
        The directory, as a string.
    """
    files = matchers if isinstance(matchers, dict) else {m: {"id": m, **_MATCHER} for m in matchers}
    doc = {"schema": 1, "matchers": {}, "bundles": {}}
    for name, body in files.items():
        path = root / "matchers" / "python" / name / "matcher.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(body))
        doc["matchers"][name] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": _SHA,
            "description": "d",
        }
    for name, include in (bundles or {}).items():
        path = root / "bundles" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"id": name, "description": "A bundle for the loading tests.", "include": include})
        )
        doc["bundles"][name] = {"path": f"bundles/{name}.yaml", "sha256": _SHA, "description": "d"}
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.yaml").write_text(yaml.safe_dump(index if index is not None else doc))
    return str(root)


def _http(files, base="https://reg.test/m/", **headers_by_path):
    """A client whose transport serves `files` (`{relative path: text}`) under `base`."""

    def handler(request):
        url = str(request.url)
        if url in headers_by_path:
            status, location = headers_by_path[url]
            return httpx.Response(status, headers={"location": location})
        rel = url.removeprefix(base)
        if url.startswith(base) and rel in files:
            return httpx.Response(200, text=files[rel])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _fixture_files():
    """The committed fixture registry, as `{relative path: text}` for an HTTP mock."""
    root = support.REGISTRY
    return {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob("*.yaml")}


# --- resolution ------------------------------------------------------------------


def test_a_nested_bundle_flattens_and_ids_carry_the_alias():
    """`all` -> `python` -> two matchers, renamed `alias/id`."""
    matchers = load(_cfg(["fx/all"], fx=str(support.REGISTRY)))
    assert [m.id for m in matchers] == ["fx/pytest", "fx/python_traceback"]


def test_a_matcher_listed_directly_and_in_a_bundle_loads_once():
    """Dedupe by `alias/name`: the direct listing keeps its place."""
    matchers = load(_cfg(["fx/pytest", "fx/python"], fx=str(support.REGISTRY)))
    assert [m.id for m in matchers] == ["fx/pytest", "fx/python_traceback"]


def test_resolve_records_how_each_matcher_got_in():
    """`matchers list` shows the bundle a matcher came through, or `direct`."""
    resolved = resolve(_cfg(["fx/pytest", "fx/all"], fx=str(support.REGISTRY)))
    assert resolved.matchers == {"fx/pytest": "direct", "fx/python_traceback": "fx/all"}


def test_the_same_bare_id_from_two_registries_is_two_matchers(tmp_path):
    """`a/x` and `b/x` are distinct; extract merges their windows if they overlap."""
    a, b = _registry(tmp_path / "a", ["x"]), _registry(tmp_path / "b", ["x"])
    assert [m.id for m in load(_cfg(["a/x", "b/x"], a=a, b=b))] == ["a/x", "b/x"]


def test_a_bundle_cycle_is_a_setup_error(tmp_path):
    """Hand-written index: `matchers index` would refuse to build it."""
    reg = _registry(tmp_path, ["x"], bundles={"p": ["q"], "q": ["p"]})
    with pytest.raises(SetupError, match="bundle cycle: r/p -> r/q -> r/p"):
        load(_cfg(["r/p"], r=reg))


def test_nesting_deeper_than_five_is_a_setup_error(tmp_path):
    """b1 -> b2 -> ... -> b6 -> x."""
    bundles = {f"b{i}": [f"b{i + 1}"] for i in range(1, 6)} | {"b6": ["x"]}
    with pytest.raises(SetupError, match="nests deeper than 5"):
        load(_cfg(["r/b1"], r=_registry(tmp_path, ["x"], bundles=bundles)))


def test_an_empty_include_says_how_to_pick_matchers():
    """The exact next step, before anything is fetched."""
    with pytest.raises(SetupError, match=r"Run `ci-doctor matchers add official/<name>`"):
        load(_cfg([], official="https://matchers.cidoc.dev/"))


def test_an_unknown_alias_lists_the_configured_ones():
    """A typo in the alias names what exists."""
    with pytest.raises(SetupError, match="unknown registry 'acme'.*configured: fx"):
        load(_cfg(["acme/pytest"], fx=str(support.REGISTRY)))


def test_an_unknown_name_suggests_the_closest(tmp_path):
    """`pytset` is one transposition from `pytest`."""
    with pytest.raises(SetupError, match="did you mean fx/pytest"):
        load(_cfg(["fx/pytset"], fx=str(support.REGISTRY)))


# --- locations and transports ----------------------------------------------------


@pytest.mark.parametrize("form", ["{p}", "{p}/", "file://{p}", "file://{p}/"])
def test_a_local_registry_by_path_or_file_url(form):
    """Bare paths and `file://`, with or without the trailing slash, need no network."""
    location = form.format(p=support.REGISTRY)
    assert len(load(_cfg(["fx/all"], fx=location))) == 2


@pytest.mark.parametrize(
    "location", ["https://reg.test/m/index.yaml", "/srv/matchers/index.yaml", "/srv/matchers/index.yaml/"]
)
def test_a_location_naming_the_index_file_is_refused(location):
    """Point at the folder; ci-doctor appends `index.yaml` itself."""
    with pytest.raises(SetupError, match="point at the registry folder"):
        load(_cfg(["r/x"], r=location))


def test_an_unsupported_scheme_is_refused():
    """Only paths, `file://` and `http(s)://` until the git and S3 transports land."""
    with pytest.raises(SetupError, match="unsupported scheme 'ftp'"):
        load(_cfg(["r/x"], r="ftp://reg.test/m/"))


def test_an_https_registry_loads_through_the_shared_client():
    """The trailing slash is added, so `index.yaml` joins under the folder."""
    matchers = load(_cfg(["fx/all"], fx="https://reg.test/m"), client=_http(_fixture_files()))
    assert [m.id for m in matchers] == ["fx/pytest", "fx/python_traceback"]


def test_a_plain_http_registry_loads_and_warns(caplog):
    """Allowed — an internal server without TLS is the user's call — but never silently."""
    client = _http(_fixture_files(), base="http://reg.internal/m/")
    with caplog.at_level(logging.WARNING, logger="ci_doctor.matchers"):
        assert len(load(_cfg(["fx/all"], fx="http://reg.internal/m/"), client=client)) == 2
    assert any("unencrypted" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("target", "match"),
    [
        ("http://reg.test/m/index.yaml", "redirect from https"),
        ("https://evil.test/index.yaml", "another host"),
    ],
)
def test_a_redirect_may_not_downgrade_or_change_host(target, match):
    """A registry the user put on https stays on https, on its own host."""
    client = _http({}, **{"https://reg.test/m/index.yaml": (302, target)})
    with pytest.raises(SetupError, match=match):
        load(_cfg(["fx/all"], fx="https://reg.test/m/"), client=client)


def test_a_redirect_on_the_same_host_is_followed():
    """A moved folder on the same host still works."""
    files = {f"v2/{k}": v for k, v in _fixture_files().items()}
    client = _http(
        files, base="https://reg.test/", **{"https://reg.test/m/index.yaml": (301, "/v2/index.yaml")}
    )
    # Only the index is redirected; every other file is fetched under the configured folder.
    files.update({f"m/{k}": v for k, v in _fixture_files().items()})
    assert len(load(_cfg(["fx/all"], fx="https://reg.test/m/"), client=client)) == 2


@pytest.mark.parametrize("missing", ["index.yaml", "matchers/python/pytest/matcher.yaml"])
def test_a_404_names_the_registry_and_the_file(missing):
    """Unreachable index and missing matcher file both stop the setup."""
    files = _fixture_files()
    del files[missing]
    with pytest.raises(SetupError, match=f"registry 'fx' .*GET {missing}: HTTP 404"):
        load(_cfg(["fx/all"], fx="https://reg.test/m/"), client=_http(files))


def test_an_unreachable_registry_is_a_setup_error():
    """A transport failure is named, not a traceback."""

    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(down))
    with pytest.raises(SetupError, match="registry 'fx' .*connection refused"):
        load(_cfg(["fx/all"], fx="https://reg.test/m/"), client=client)


def test_a_missing_local_registry_is_a_setup_error(tmp_path):
    """Same message shape as the network case."""
    with pytest.raises(SetupError, match="registry 'r' .*index.yaml"):
        load(_cfg(["r/x"], r=str(tmp_path / "nowhere")))


def test_credentials_in_a_location_are_redacted():
    """A setup error lands in the job log; the token must not."""
    with pytest.raises(SetupError) as exc:
        load(_cfg(["fx/all"], fx="https://bot:s3cret@reg.test/m/"), client=_http({}))
    assert "s3cret" not in str(exc.value)
    assert "https://***@reg.test/m/" in str(exc.value)


# --- bounds ----------------------------------------------------------------------


def test_an_oversized_index_is_cut_off_while_streaming():
    """Bounded memory: the body is abandoned past the cap, not parsed."""
    client = _http({"index.yaml": "#" * (INDEX_CAP + 1)})
    with pytest.raises(SetupError, match="over the 1048576-byte cap"):
        load(_cfg(["fx/all"], fx="https://reg.test/m/"), client=client)


def test_an_oversized_matcher_file_is_refused(tmp_path):
    """Local files are checked by size before they are read."""
    reg = _registry(tmp_path, ["x"])
    (tmp_path / "matchers/python/x/matcher.yaml").write_text("#" * (FILE_CAP + 1))
    with pytest.raises(SetupError, match="over the 65536-byte cap"):
        load(_cfg(["r/x"], r=reg))


def test_an_alias_bomb_index_is_refused(tmp_path):
    """Anchors and aliases are refused outright, however small the file."""
    root = tmp_path / "r"
    root.mkdir()
    (root / "index.yaml").write_text("a: &a [x, x]\nb: &b [*a, *a]\nschema: 1\n")
    with pytest.raises(SetupError, match="anchors and aliases"):
        load(_cfg(["r/x"], r=str(root)))


@pytest.mark.parametrize("path", ["../outside.yaml", "/etc/passwd.yaml", "https://evil.test/x.yaml"])
def test_an_index_path_cannot_leave_the_registry(tmp_path, path):
    """A hostile index can't make ci-doctor read `/etc` or fetch another host."""
    doc = {"schema": 1, "matchers": {"x": {"path": path, "sha256": _SHA, "description": "d"}}}
    with pytest.raises(SetupError, match="index.yaml"):
        load(_cfg(["r/x"], r=_registry(tmp_path, index=doc)))


def test_a_symlink_cannot_leave_a_local_registry(tmp_path):
    """The path text passes; the real path does not."""
    (tmp_path / "secret.yaml").write_text(yaml.safe_dump({"id": "x", **_MATCHER}))
    reg = _registry(tmp_path / "r", ["x"])
    target = tmp_path / "r/matchers/python/x/matcher.yaml"
    target.unlink()
    target.symlink_to(tmp_path / "secret.yaml")
    with pytest.raises(SetupError, match="outside the registry"):
        load(_cfg(["r/x"], r=reg))


def test_a_matcher_breaking_a_field_rule_is_refused_at_load(tmp_path):
    """A self-hosted registry may never have run `verify`; the load-time rules still hold."""
    reg = _registry(
        tmp_path, {"x": {"id": "x", **_MATCHER, "description": "Hidden \u202e text in a description."}}
    )
    with pytest.raises(SetupError, match="printable ASCII"):
        load(_cfg(["r/x"], r=reg))


def test_a_matcher_whose_id_disagrees_with_its_index_name_is_refused(tmp_path):
    """The index says `x`; the file says `y`: one of them is lying."""
    reg = _registry(tmp_path, {"x": {"id": "y", **_MATCHER}})
    with pytest.raises(SetupError, match="does not match its index name 'x'"):
        load(_cfg(["r/x"], r=reg))
