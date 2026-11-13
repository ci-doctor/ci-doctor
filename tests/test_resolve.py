"""Finding the run a caller means: every row of the plan's resolution table.

The CI side is a fake adapter that answers `find_runs` from a list, so the git
side can be real: temporary checkouts, real commits, real remotes. No network.
"""

import shutil
import subprocess

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import RunSummary
from ci_doctor.core.ports import CIProvider
from ci_doctor.providers import git_origin
from ci_doctor.providers.github.urls import parse_run_url as gh_url
from ci_doctor.providers.gitlab.urls import parse_run_url as gl_url
from ci_doctor.service.resolve import Outcome, resolve

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


class FakeCI(CIProvider):
    """Answers `find_runs` from a list of runs, newest first. A PR's runs carry `ref="pr-<n>"`."""

    def __init__(self, runs):
        """Serve `runs`."""
        self.runs = runs

    def fetch_run(self, run_ref):
        """Never called: resolving lists runs, it does not fetch them."""
        raise AssertionError("resolve never fetches a run")

    def fetch_job_log(self, job):
        """Never called."""
        raise AssertionError("resolve never fetches a log")

    def find_runs(self, *, sha=None, branch=None, mr=None, limit=20):
        """Filter the way a CI API would: by commit, branch, or a PR's ref."""
        if mr is not None:
            return [r for r in self.runs if r.ref == f"pr-{mr}"][:limit]
        return [r for r in self.runs if (r.sha == sha if sha is not None else r.ref == branch)][:limit]

    def run_of_job(self, job_id):
        """Every job belongs to a run named after it."""
        return f"run-of-{job_id}"


@pytest.fixture
def ci(monkeypatch):
    """Serve the runs a test sets on `ci.runs`; `ci.made` records each (vendor, repo, cwd) built."""
    fake = FakeCI([])
    fake.made = []

    def make(cfg, vendor=None, *, repo=None, cwd=None):
        fake.made.append((vendor, repo, cwd))
        return fake

    monkeypatch.setattr("ci_doctor.service.resolve.make_ci_provider", make)
    return fake


def _git(path, *args):
    """Run git in `path` and return its output."""
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S607 - git off PATH


@pytest.fixture
def checkout(tmp_path):
    """A checkout on `main` with one commit, its origin on github.com and one pushed branch."""
    git_origin.origin_repo.cache_clear()
    path = tmp_path / "app"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "one")
    _git(path, "remote", "add", "origin", "git@github.com:acme/app.git")
    return path


def _push(path):
    """Mark HEAD as pushed: a remote-tracking ref that contains it, without a network."""
    _git(path, "update-ref", "refs/remotes/origin/main", "HEAD")


def _run(id_, sha, status, ref="main"):
    """A run summary with only what resolving reads."""
    return RunSummary(id=id_, name="ci", ref=ref, sha=sha, status=status)


def _resolve(target, workspace):
    """Resolve with the shipped config."""
    return resolve(target, load_config(environ={}), workspace)[0]


def _head(path):
    """The checkout's commit."""
    return _git(path, "rev-parse", "HEAD")


def test_failed_runs_for_head_are_found_all_of_them(checkout, ci):
    """Several workflows can build one commit: every failed one is analyzed."""
    sha = _head(checkout)
    ci.runs = [_run("3", sha, "failed"), _run("2", sha, "success"), _run("1", sha, "failed")]
    res = _resolve(None, checkout)
    assert (res.outcome, res.run_ids, res.vendor, res.repo) == (
        Outcome.FOUND,
        ("3", "1"),
        "github",
        "acme/app",
    )
    assert ci.made == [("github", "acme/app", checkout)]


def test_a_running_run_with_a_failed_one_is_found_and_marked_in_progress(checkout, ci):
    """Jobs that already failed are worth analyzing while the rest still run."""
    sha = _head(checkout)
    ci.runs = [_run("2", sha, "running"), _run("1", sha, "failed")]
    res = _resolve(None, checkout)
    assert (res.outcome, res.run_ids, res.in_progress) == (Outcome.FOUND, ("1", "2"), True)


def test_only_running_is_still_running_with_the_branchs_last_failure_listed(checkout, ci):
    """Nothing has failed yet: say so, and point at the last failure without analyzing it."""
    ci.runs = [_run("9", _head(checkout), "running"), _run("5", "oldsha", "failed")]
    res = _resolve(None, checkout)
    assert (res.outcome, res.run_ids) == (Outcome.STILL_RUNNING, ("9",))
    assert res.earlier_failure is not None and res.earlier_failure.id == "5"


def test_a_clean_newest_run_is_no_failure(checkout, ci):
    """The newest run passed: nothing to fix."""
    ci.runs = [_run("4", _head(checkout), "success")]
    res = _resolve(None, checkout)
    assert res.outcome == Outcome.NO_FAILURE and res.newest is not None and res.newest.id == "4"


def test_an_unpushed_head_says_so(checkout, ci):
    """No run and no remote branch holds HEAD: the fix is to push, not to look harder."""
    ci.runs = [_run("5", "oldsha", "failed")]
    assert _resolve(None, checkout).outcome == Outcome.NOT_PUSHED


def test_pushed_but_no_run_falls_back_to_the_branchs_newest_failure(checkout, ci):
    """CI has not picked HEAD up yet: the branch's newest failure, with a note saying so."""
    _push(checkout)
    ci.runs = [_run("5", "oldsha", "failed")]
    res = _resolve(None, checkout)
    assert (res.outcome, res.run_ids) == (Outcome.FOUND, ("5",))
    assert any("no run for HEAD" in n for n in res.notes)


def test_a_detached_head_resolves_by_commit(checkout, ci):
    """No branch is no problem when the commit has runs."""
    sha = _head(checkout)
    _git(checkout, "checkout", "-q", "--detach")
    ci.runs = [_run("7", sha, "failed")]
    assert _resolve(None, checkout).run_ids == ("7",)


def test_a_fork_uses_its_upstream(checkout, ci):
    """`origin` is the fork; the PR's CI ran in upstream, so that is the repo asked."""
    _git(checkout, "remote", "set-url", "origin", "git@github.com:me/app.git")
    _git(checkout, "remote", "add", "upstream", "git@github.com:acme/app.git")
    ci.runs = [_run("1", _head(checkout), "failed")]
    res = _resolve(None, checkout)
    assert res.repo == "acme/app" and ci.made[0][1] == "acme/app"
    assert any("upstream" in n for n in res.notes)


def test_a_pr_number_uses_its_newest_commits_runs(checkout, ci):
    """`#12` means the runs for the PR's latest push, not every push it ever had."""
    ci.runs = [_run("8", "new", "failed", ref="pr-12"), _run("6", "old", "failed", ref="pr-12")]
    assert _resolve("#12", checkout).run_ids == ("8",)


def test_a_run_id_is_taken_as_given(checkout, ci):
    """A bare id needs no lookup."""
    res = _resolve("123456", checkout)
    assert (res.outcome, res.run_ids) == (Outcome.FOUND, ("123456",))


def test_no_target_and_no_workspace_says_so(ci):
    """Nothing to go on is an answer, not a guess."""
    res, provider = resolve(None, load_config(environ={}), None)
    assert res.outcome == Outcome.NO_WORKSPACE and provider is None and ci.made == []


def test_a_remote_on_an_unconfigured_host_falls_back_to_ci_and_says_so(checkout, ci):
    """A self-hosted remote nobody configured still works through `ci`, with a note."""
    _git(checkout, "remote", "set-url", "origin", "git@git.corp:team/app.git")
    res = _resolve("1", checkout)
    assert res.vendor == "github"
    assert any("git.corp is not a configured CI host" in n for n in res.notes)


# --- URLs -------------------------------------------------------------------------


def test_a_run_url_names_the_repo_and_run_whatever_the_checkout(checkout, ci):
    """The URL wins: its repository, its run, its job."""
    res = _resolve("https://github.com/other/thing/actions/runs/42/job/7", checkout)
    assert (res.outcome, res.repo, res.run_ids, res.job_id) == (Outcome.FOUND, "other/thing", ("42",), "7")
    assert ci.made[0][:2] == ("github", "other/thing")


def test_a_gitlab_job_url_finds_its_pipeline(ci):
    """A job URL names no pipeline: the adapter looks it up."""
    res = _resolve("https://gitlab.com/g/sub/p/-/jobs/55", None)
    assert (res.vendor, res.repo, res.run_ids, res.job_id) == ("gitlab", "g/sub/p", ("run-of-55",), "55")


def test_a_url_on_an_unconfigured_host_is_refused_before_any_adapter_exists(ci):
    """Nothing is built, so no token can go anywhere; the note says what to configure."""
    res, provider = resolve("https://attacker.example/acme/app/actions/runs/1", load_config(environ={}), None)
    assert res.outcome == Outcome.NOT_FOUND and provider is None and ci.made == []
    assert "attacker.example" in res.notes[0]


def test_a_url_that_is_not_a_run_is_refused(ci):
    """A repo page is on the right host but names no run."""
    res, provider = resolve("https://github.com/acme/app/pulls", load_config(environ={}), None)
    assert res.outcome == Outcome.NOT_FOUND and provider is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/a/b/actions/runs/1", ("a/b", "1", None)),
        ("https://github.com/a/b/actions/runs/1/attempts/2", ("a/b", "1", None)),
        ("https://github.com/a/b/actions/runs/1/job/9", ("a/b", "1", "9")),
        ("https://github.com/a/b/runs/9", ("a/b", None, "9")),
        ("https://github.com/a/b/actions", None),
        ("https://ghe.corp/a/b/actions/runs/1", None),  # another host is not ours
    ],
)
def test_github_url_shapes(url, expected):
    """Every link the Actions UI hands out, and nothing else."""
    ref = gh_url(url, "github.com")
    assert (ref and (ref.repo, ref.run_id, ref.job_id)) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://gitlab.com/g/p/-/pipelines/5", ("g/p", "5", None)),
        ("https://gitlab.com/g/sub/p/-/pipelines/5/failures", ("g/sub/p", "5", None)),
        ("https://gitlab.com/g/sub/p/-/jobs/8", ("g/sub/p", None, "8")),
        ("https://gitlab.com/g/p/-/merge_requests/3", None),
        ("https://other.example/g/p/-/pipelines/5", None),
    ],
)
def test_gitlab_url_shapes(url, expected):
    """Pipeline and job links, nested groups kept whole."""
    ref = gl_url(url, "gitlab.com")
    assert (ref and (ref.repo, ref.run_id, ref.job_id)) == expected
