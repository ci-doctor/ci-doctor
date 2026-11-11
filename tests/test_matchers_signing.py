"""The official registry's signed index: what verifies, what is refused, and why.

Offline: a fixed test key (`tests.support`) is the only one trusted, and the
official registry is served from disk or through `httpx.MockTransport`.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.mirror import mirror
from ci_doctor.matchers.registry import load
from ci_doctor.matchers.signing import SIGNATURE, sign, verify
from tests import support

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
KEYS = {support.TEST_KEY_ID: support.TEST_PUBLIC_KEY}
INDEX = b"schema: 1\nmatchers: {}\n"


def _sig(
    index: bytes = INDEX, *, version: int = 7, expires: datetime | None = None, key_id: str = "test"
) -> bytes:
    until = expires or NOW + timedelta(days=30)
    return sign(index, version=version, expires=until, key_id=key_id, private_key=support.TEST_PRIVATE_KEY)


def test_a_signature_vouches_for_its_index_version_and_expiry():
    """The happy path: the claims come back."""
    signed = verify(INDEX, _sig(), KEYS, NOW)
    assert (signed.version, signed.expires) == (7, NOW + timedelta(days=30))


def test_a_key_this_ci_doctor_does_not_ship_is_refused():
    """A rotated-in key needs a ci-doctor that knows it."""
    with pytest.raises(SetupError, match="signed with key 'next', which this ci-doctor doesn't trust"):
        verify(INDEX, _sig(key_id="next"), KEYS, NOW)


def test_a_signature_by_another_key_is_refused():
    """Same key id, wrong key: whoever signed it isn't the official registry."""
    other = base64.b64encode(b"\x01" * 32).decode()
    forged = sign(INDEX, version=7, expires=NOW + timedelta(days=1), key_id="test", private_key=other)
    with pytest.raises(SetupError, match="doesn't verify"):
        verify(INDEX, forged, KEYS, NOW)


def test_an_index_changed_after_signing_is_refused():
    """The signature names the index's hash; a swapped index doesn't match it."""
    with pytest.raises(SetupError, match="index.yaml has sha256 .*, but its signature is for"):
        verify(INDEX + b"# swapped\n", _sig(), KEYS, NOW)


def test_an_expired_signature_is_refused():
    """A frozen copy stops working once the registry's re-signing would have replaced it."""
    with pytest.raises(SetupError, match="the signed index expired at 2026-11-10"):
        verify(INDEX, _sig(expires=NOW - timedelta(days=1)), KEYS, NOW)


@pytest.mark.parametrize(
    "body", [b"not json", b"{}", json.dumps({"key_id": "test", "payload": "!!", "signature": ""}).encode()]
)
def test_a_malformed_signature_is_refused(body):
    """Never a traceback: the file is named."""
    with pytest.raises(SetupError, match=f"{SIGNATURE} is malformed"):
        verify(INDEX, body, KEYS, NOW)


# --- through the loader -----------------------------------------------------------------


def _official(root, tmp_path, include=("official/all",)) -> MatchersConfig:
    return MatchersConfig(
        registries={"official": str(root)}, include=list(include), cache_dir=str(tmp_path / "c")
    )


def test_the_official_registry_must_publish_a_signature(tmp_path):
    """No `index.yaml.sig`, no official matchers: the setup fails naming the file."""
    root = support.py_registry(tmp_path / "reg")
    with pytest.raises(SetupError, match=f"registry 'official' .*{SIGNATURE}"):
        load(_official(root, tmp_path))


def test_a_signed_official_registry_loads(tmp_path):
    """Signed with the trusted key: it loads like any registry."""
    root = support.sign_registry(support.py_registry(tmp_path / "reg"))
    assert len(load(_official(root, tmp_path))) == 2


def test_an_older_signed_index_than_one_already_seen_is_refused(tmp_path):
    """Rollback: the cache remembers the highest version, so a replayed older index fails."""
    root = support.sign_registry(support.py_registry(tmp_path / "reg"), version=5)
    load(_official(root, tmp_path))
    support.sign_registry(root, version=4)
    with pytest.raises(
        SetupError, match="version 4, older than version 5 already seen: refused as a rollback"
    ):
        load(_official(root, tmp_path))
    support.sign_registry(root, version=6)
    assert len(load(_official(root, tmp_path))) == 2


def test_another_registry_is_never_signature_checked(tmp_path):
    """A private registry is trusted as served; only `official` carries the check."""
    root = support.py_registry(tmp_path / "reg")
    cfg = MatchersConfig(registries={"acme": str(root)}, include=["acme/all"], cache_dir=str(tmp_path / "c"))
    assert len(load(cfg)) == 2


def test_a_re_signed_index_is_picked_up_even_when_the_index_is_unchanged(tmp_path):
    """The weekly re-sign changes only the signature: a revalidated index fetches it again."""
    root = support.sign_registry(support.py_registry(tmp_path / "reg"), version=1)
    files = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        rel = request.url.path.removeprefix("/m/")
        seen.append(rel)
        body = files[rel].read_bytes()
        tag = f'"{hash(body)}"'
        if request.headers.get("if-none-match") == tag:
            return httpx.Response(304, headers={"etag": tag})
        return httpx.Response(200, headers={"etag": tag}, content=body)

    cfg = MatchersConfig(
        registries={"official": "https://reg.test/m/"},
        include=["official/all"],
        cache_dir=str(tmp_path / "c"),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    load(cfg, client=client)
    support.sign_registry(root, version=2)
    seen.clear()
    load(cfg, client=client)
    assert seen == ["index.yaml", SIGNATURE]  # the index 304s; the new signature is fetched


def test_a_mirror_of_the_official_registry_still_verifies_as_official(tmp_path):
    """`mirror` copies the signature, so the copy can stand in for `official`."""
    root = support.sign_registry(support.py_registry(tmp_path / "reg"))
    dest = tmp_path / "copy"
    assert mirror(_official(root, tmp_path), "official", dest) == 4
    assert (dest / SIGNATURE).read_bytes() == (root / SIGNATURE).read_bytes()
    assert len(load(_official(dest, tmp_path / "other"))) == 2
