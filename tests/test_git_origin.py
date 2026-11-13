"""Repository identity from a local checkout: which checkout, which remote, which host.

Real git, real temporary repositories: no network is touched, and the parsing is
proved against what git itself prints rather than a hand-written stub.
"""

import shutil
import subprocess

import pytest

from ci_doctor.providers import git_origin
from ci_doctor.providers.git_origin import Remote, head, parse_remote_url, remote

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _repo(path, **remotes):
    """Make a checkout at `path` with one commit and the given remotes."""
    path.mkdir()

    def run(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)  # noqa: S607 - git off PATH, as in the code under test

    run("init", "-q", "-b", "main")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first")
    for name, url in remotes.items():
        run("remote", "add", name, url)
    return path


@pytest.fixture(autouse=True)
def _fresh_cache():
    """`origin_repo` caches per checkout; each test starts clean."""
    git_origin.origin_repo.cache_clear()
    yield
    git_origin.origin_repo.cache_clear()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:acme/app.git", ("github.com", "acme/app")),
        ("https://GitLab.Example.com/group/sub/project.git", ("gitlab.example.com", "group/sub/project")),
        ("ssh://git@ghe.internal:2222/acme/app", ("ghe.internal", "acme/app")),
        ("https://user@git.example.com:8443/team/repo", ("git.example.com", "team/repo")),
        ("/srv/git/app.git", None),
        ("", None),
    ],
)
def test_a_remote_url_splits_into_host_and_repo(url, expected):
    """Every URL shape git accepts gives the same host and path; a local path gives neither."""
    assert parse_remote_url(url) == expected


def test_each_checkout_answers_for_itself(tmp_path):
    """Two checkouts, two answers: nothing leaks between them through the cache."""
    a = _repo(tmp_path / "a", origin="git@github.com:acme/a.git")
    b = _repo(tmp_path / "b", origin="https://gitlab.com/team/b.git")
    assert git_origin.origin_repo("GITHUB_REPOSITORY", a) == "acme/a"
    assert git_origin.origin_repo("CI_PROJECT_ID", b) == "team/b"


def test_a_fork_can_read_its_upstream(tmp_path):
    """`origin` is the fork; `upstream` names the repository whose CI ran the PR."""
    repo = _repo(
        tmp_path / "fork", origin="git@github.com:me/app.git", upstream="git@github.com:acme/app.git"
    )
    assert remote("origin", repo) == Remote("origin", "github.com", "me/app")
    assert remote("upstream", repo) == Remote("upstream", "github.com", "acme/app")
    assert remote("nope", repo) is None


def test_head_reports_branch_and_commit_and_detached_has_no_branch(tmp_path):
    """A branch checkout names its branch; a detached HEAD still has a commit."""
    repo = _repo(tmp_path / "r")
    here = head(repo)
    assert here.branch == "main"
    assert here.sha is not None and len(here.sha) == 40
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)  # noqa: S607 - git off PATH
    assert head(repo) == git_origin.Head(branch=None, sha=here.sha)


def test_outside_a_checkout_everything_is_none(tmp_path):
    """No checkout is an answer, not an error."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert remote("origin", plain) is None
    assert head(plain) == git_origin.Head(branch=None, sha=None)
    assert git_origin.origin_repo("GITHUB_REPOSITORY", plain) is None
