"""`git+` and `s3://` registries, offline: a local repo stands in for the remote, botocore is stubbed."""

import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ci_doctor.config.schema import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers import transports
from ci_doctor.matchers.registry import INDEX_CAP, load, registry_dir

FIXTURE = Path(__file__).parent / "fixtures" / "registry"
needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


def _cfg(location):
    return MatchersConfig(registries={"r": location}, include=["r/pytest"])


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(transports, "_checkouts", {})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A bare repo holding the fixture registry at its root and under `reg/`, tagged v1.

    The `file` protocol is allowed for the test only; the real allowlist refuses it.
    """
    monkeypatch.setattr(transports, "_PROTOCOLS", ("https", "ssh", "file"))
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):  # set when run from a git hook
        monkeypatch.delenv(var, raising=False)
    work, bare = tmp_path / "work", tmp_path / "bare.git"
    shutil.copytree(FIXTURE, work)
    shutil.copytree(FIXTURE, work / "reg")

    def git(*args, cwd=work):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout  # noqa: S607

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
    git("tag", "v1")
    sha = git("rev-parse", "HEAD").strip()
    git("clone", "-q", "--bare", str(work), str(bare), cwd=tmp_path)
    return f"git+file://{bare}", sha


@needs_git
@pytest.mark.parametrize("ref", ["", "@main", "@v1", "@SHA"])
def test_git_registry_loads_at_default_branch_tag_or_sha(repo, ref):
    """Git registry loads at default branch tag or sha."""
    url, sha = repo
    loaded = load(_cfg(url + ref.replace("SHA", sha)))
    assert [m.id for m in loaded] == ["r/pytest"]


@needs_git
def test_git_subdirectory(repo):
    """Git subdirectory."""
    assert load(_cfg(repo[0] + "#subdirectory=reg"))[0].id == "r/pytest"


@needs_git
def test_git_subdirectory_outside_the_checkout_is_refused(repo):
    """Git subdirectory outside the checkout is refused."""
    with pytest.raises(SetupError, match="outside the registry"):
        load(_cfg(repo[0] + "#subdirectory=../.."))


@needs_git
def test_git_missing_ref_fails_naming_the_registry(repo):
    """Git missing ref fails naming the registry."""
    with pytest.raises(SetupError, match="registry 'r'.*git fetch failed"):
        load(_cfg(repo[0] + "@nope"))


@needs_git
def test_git_checkout_is_cloned_once_per_run(repo, monkeypatch):
    """Git checkout is cloned once per run."""
    calls = []
    real = transports._git
    monkeypatch.setattr(transports, "_git", lambda args, *rest: (calls.append(args[0]), real(args, *rest))[1])
    load(MatchersConfig(registries={"r": repo[0]}, include=["r/pytest", "r/python_traceback"]))
    assert calls.count("fetch") == 1


@needs_git
def test_git_checkout_over_the_size_cap_fails(repo, monkeypatch):
    """Git checkout over the size cap fails."""
    monkeypatch.setattr(transports, "MAX_CHECKOUT_BYTES", 10)
    with pytest.raises(SetupError, match="over the 10-byte cap"):
        load(_cfg(repo[0]))


@pytest.mark.parametrize("value", ["git+file:///tmp/x.git", "git+ext::sh -c id", "git+http://h/r.git"])
def test_git_transports_outside_the_allowlist_are_refused(value):
    """Git transports outside the allowlist are refused."""
    with pytest.raises(SetupError, match="registry 'r'"):
        registry_dir("r", value)


def test_scp_style_is_refused_with_a_hint():
    """Scp style is refused with a hint."""
    with pytest.raises(SetupError, match=r"git\+ssh:// URL"):
        registry_dir("r", "git@host:group/repo.git")


def test_git_missing_from_path(monkeypatch):
    """Git missing from path."""
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SetupError, match=r"git\+ registries need git on PATH"):
        load(_cfg("git+https://host/g/r.git"))


@needs_git
def test_git_credentials_are_redacted():
    """Git credentials are redacted."""
    with pytest.raises(SetupError) as err:
        load(_cfg("git+https://user:s3cret@127.0.0.1:1/g/r.git"))
    assert "s3cret" not in str(err.value)


def test_new_schemes_are_accepted_and_unknown_still_refused():
    """New schemes are accepted and unknown still refused."""
    assert registry_dir("r", "s3://bucket/pre") == "s3://bucket/pre/"
    git_url = "git+https://h/r.git@v1#subdirectory=a"
    assert registry_dir("r", git_url) == git_url
    with pytest.raises(SetupError, match="unsupported scheme 'ftp'"):
        registry_dir("r", "ftp://h/x")
    with pytest.raises(SetupError, match="needs a bucket"):
        registry_dir("r", "s3://")


# --- s3 --------------------------------------------------------------------------

boto3 = pytest.importorskip("boto3", reason="boto3 not installed")
from botocore.response import StreamingBody
from botocore.stub import Stubber


@pytest.fixture
def s3(monkeypatch):
    """A stubbed client; fake credentials so botocore never looks for real ones."""
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="k", aws_secret_access_key="s")  # noqa: S106
    monkeypatch.setattr(transports, "_s3_client", lambda: client)
    with Stubber(client) as stub:
        yield stub


def _get(stub, key, text):
    raw = text.encode()
    body = {"Body": StreamingBody(io.BytesIO(raw), len(raw))}
    stub.add_response("get_object", body, {"Bucket": "b", "Key": key})


def test_s3_registry_loads_end_to_end(s3):
    """S3 registry loads end to end."""
    _get(s3, "pre/index.yaml", (FIXTURE / "index.yaml").read_text())
    path = "matchers/python/pytest/matcher.yaml"
    _get(s3, f"pre/{path}", (FIXTURE / path).read_text())
    assert [m.id for m in load(_cfg("s3://b/pre/"))] == ["r/pytest"]
    s3.assert_no_pending_responses()


def test_s3_missing_key_names_the_registry(s3):
    """S3 missing key names the registry."""
    s3.add_client_error("get_object", "NoSuchKey", expected_params={"Bucket": "b", "Key": "pre/index.yaml"})
    with pytest.raises(SetupError, match="registry 'r'.*NoSuchKey"):
        load(_cfg("s3://b/pre/"))


def test_s3_object_over_the_cap_fails(s3):
    """S3 object over the cap fails."""
    _get(s3, "pre/index.yaml", "x" * (INDEX_CAP + 1))
    with pytest.raises(SetupError, match="byte cap"):
        load(_cfg("s3://b/pre/"))


def test_s3_extra_missing(monkeypatch):
    """S3 extra missing."""
    transports._s3_client.cache_clear()
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(SetupError, match=r"pip install ci-doctorr\[s3\]"):
        load(_cfg("s3://b/pre/"))
