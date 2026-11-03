"""`git+` registries, offline: a local repo stands in for the remote."""

import shutil
import subprocess

import pytest

from ci_doctor.config.schema import MatchersConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers import transports
from ci_doctor.matchers.registry import load, registry_dir
from tests import support

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


def _cfg(location):
    return MatchersConfig(registries={"r": location}, include=["r/pytest"])


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(transports, "_checkouts", {})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A bare repo holding a two-matcher registry at its root and under `reg/`, tagged v1.

    The `file` protocol is allowed for the test only; the real allowlist refuses it.
    """
    monkeypatch.setattr(transports, "_PROTOCOLS", ("https", "ssh", "file"))
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):  # set when run from a git hook
        monkeypatch.delenv(var, raising=False)
    work, bare = tmp_path / "work", tmp_path / "bare.git"
    support.py_registry(work)
    support.py_registry(work / "reg")

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


def test_git_schemes_are_accepted_and_unknown_still_refused():
    """Git schemes are accepted and unknown still refused."""
    git_url = "git+https://h/r.git@v1#subdirectory=a"
    assert registry_dir("r", git_url) == git_url
    with pytest.raises(SetupError, match="unsupported scheme 'ftp'"):
        registry_dir("r", "ftp://h/x")
