"""GitHub adapter: segmenter, reasons, provider mapping, attribution end to end.

The full run through the GitHub path proves core needed no changes for it.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import requests

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job, Phase, RunQuery
from ci_doctor.core.phases import assign_phases
from ci_doctor.core.select import select_failed_jobs
from ci_doctor.providers.github.provider import GitHubProvider
from ci_doctor.providers.github.reasons import to_failure_reason
from ci_doctor.providers.github.segmenter import GitHubSegmenter


def test_reason_mapping():
    """Conclusions map to reasons, and startup_failure wins over the conclusion."""
    assert to_failure_reason("failure") == FailureReason.SCRIPT_FAILURE
    assert to_failure_reason("timed_out") == FailureReason.TIMEOUT
    assert to_failure_reason("cancelled") == FailureReason.CANCELLED
    assert to_failure_reason(None) == FailureReason.UNKNOWN
    assert to_failure_reason("failure", startup_failure=True) == FailureReason.RUNNER_SYSTEM


def test_segmenter_groups_and_canonical_names():
    """Groups become sections with canonical names, originals kept as headers."""
    log = (
        "2024-01-01T00:00:00.0Z ##[group]Run actions/checkout@v4\n"
        "2024-01-01T00:00:01.0Z Syncing repository\n"
        "2024-01-01T00:00:02.0Z ##[endgroup]\n"
        "2024-01-01T00:00:03.0Z ##[group]Run pytest -q\n"
        "2024-01-01T00:00:04.0Z E   assert 1 == 2\n"
        "2024-01-01T00:00:05.0Z ##[endgroup]\n"
        "2024-01-01T00:00:06.0Z ##[error]Process completed with exit code 1\n"
    )
    secs = GitHubSegmenter().segment(log)
    names = [s.name for s in secs]
    assert names == ["checkout", "run", "__trailer__"]
    assert secs[0].header == "Run actions/checkout@v4"  # original name preserved
    assert secs[0].closed and secs[1].closed
    assert "exit code 1" in secs[2].lines[-1].text  # error annotation -> trailer


def test_full_attribution_through_github_path():
    """End to end on a GitHub log, with zero GitHub-specific code in core.

    The provider segmenter plus the shared phase map and classifier reach the
    right verdict — which is the whole point of the ports/adapters split.
    """
    log = (
        "##[group]Run actions/checkout@v4\n"
        "Syncing repository\n"
        "##[endgroup]\n"
        "##[group]Run pytest -q\n"
        "E   assert 1 == 2\n"
        "##[endgroup]\n"
        "##[error]Process completed with exit code 1\n"
    )
    job = Job(id="1", name="test", status="failed", failure_reason=FailureReason.SCRIPT_FAILURE, log=log)
    cfg = load_config(environ={})
    job.sections = GitHubSegmenter().segment(log)
    assign_phases(job.sections, cfg.phases)

    assert job.sections[0].phase == Phase.FETCH  # checkout -> fetch
    assert job.sections[1].phase == Phase.SCRIPT  # run -> script
    attr = attribute(job, job.sections)
    assert attr.phase == Phase.SCRIPT
    assert attr.reason == FailureReason.SCRIPT_FAILURE


def _job(**kw):
    """A stand-in for a PyGithub WorkflowJob, with every attribute we read."""
    return SimpleNamespace(
        **{
            "runner_name": None,
            "runner_id": None,
            "started_at": None,
            "completed_at": None,
            "html_url": "",
            **kw,
        }
    )


def _fake_client(jobs, *, pull_requests=()):
    """An in-memory stand-in for `github.Github`, down to the workflow run."""
    run = SimpleNamespace(
        id=999,
        head_branch="main",
        head_sha="deadbeef",
        html_url="http://gh/run/999",
        pull_requests=list(pull_requests),
        jobs=lambda: jobs,
    )
    return SimpleNamespace(get_repo=lambda name: SimpleNamespace(get_workflow_run=lambda rid: run))


def test_provider_maps_jobs_and_normalizes_status():
    """PyGithub jobs map to the domain model and failing conclusions become "failed"."""
    jobs = [
        _job(
            id=11,
            name="build",
            status="completed",
            conclusion="failure",
            runner_name="gh-runner-1",
            runner_id=7,
            html_url="http://gh/11",
            started_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        _job(id=12, name="flaky", status="completed", conclusion="timed_out"),
        _job(id=13, name="ok", status="completed", conclusion="success"),
        _job(id=14, name="slow", status="completed", conclusion="cancelled"),
    ]
    provider = GitHubProvider(
        load_config(environ={}),
        client=_fake_client(jobs),
        environ={"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/pull/42/merge"},
    )
    run = provider.fetch_run("999")

    build = run.jobs[0]
    assert build.status == "failed"  # normalized from conclusion
    assert build.failure_reason == FailureReason.SCRIPT_FAILURE
    assert build.runner.description == "gh-runner-1"
    assert build.started_at == "2024-01-01T00:00:00+00:00"  # datetime -> ISO string
    assert run.jobs[1].failure_reason == FailureReason.TIMEOUT
    assert run.jobs[2].status == "completed"  # success conclusion stays non-failed
    # A cancellation keeps its own status rather than being flattened into
    # "failed" — the domain has a word for it, and GitLab reports the same one.
    assert run.jobs[3].status == "cancelled"
    assert run.jobs[3].failure_reason == FailureReason.CANCELLED
    assert [j.name for j in select_failed_jobs(run.jobs)] == ["build", "flaky", "slow"]
    assert run.ref == "main" and run.sha == "deadbeef"  # straight off the run, no env vars
    assert run.mr is not None and run.mr.iid == "42"  # PR resolved from GITHUB_REF


def test_pull_request_on_the_run_wins_over_the_ref():
    """The run's own PR list is authoritative; GITHUB_REF is only the fork fallback."""
    provider = GitHubProvider(
        load_config(environ={}),
        client=_fake_client([], pull_requests=[SimpleNamespace(number=7)]),
        environ={"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/pull/42/merge"},
    )
    assert provider.fetch_run("999").mr.iid == "7"


def test_repository_falls_back_to_git_origin(monkeypatch):
    """With no GITHUB_REPOSITORY, the local origin names the repo (and warns)."""
    from ci_doctor.providers import git_origin

    git_origin.origin_repo.cache_clear()
    monkeypatch.setattr(
        git_origin.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="git@github.com:acme/app.git\n"),
    )
    seen = []
    provider = GitHubProvider(
        load_config(environ={}), client=SimpleNamespace(get_repo=lambda name: seen.append(name)), environ={}
    )
    provider._repo()
    assert seen == ["acme/app"]
    git_origin.origin_repo.cache_clear()


def test_origin_keeps_nested_gitlab_groups(monkeypatch):
    """A GitLab subgroup path survives; a bare regex on the tail would lose it."""
    from ci_doctor.providers import git_origin

    git_origin.origin_repo.cache_clear()
    monkeypatch.setattr(
        git_origin.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="https://gitlab.example.com/group/sub/project.git\n"
        ),
    )
    assert git_origin.origin_repo("CI_PROJECT_ID") == "group/sub/project"
    git_origin.origin_repo.cache_clear()


def test_a_failed_log_download_is_recorded_not_mistaken_for_no_log():
    """A 404 on the log leaves `log` None but says why, without the signed URL in it."""

    def refuse():
        raise requests.HTTPError(
            "404 Client Error for url: https://logs.example/1?sig=SECRET",
            response=SimpleNamespace(status_code=404),
        )

    provider = GitHubProvider(
        load_config(environ={}),
        client=_fake_client(
            [_job(id=11, name="build", status="completed", conclusion="failure", logs_url=refuse)]
        ),
        environ={"GITHUB_REPOSITORY": "acme/app"},
    )
    job = provider.fetch_run("999").jobs[0]
    assert provider.fetch_job_log(job) is None
    assert job.log_error is not None and job.log_error.startswith("HTTP 404")
    assert "SECRET" not in job.log_error


def _wr(id_, status, conclusion, sha="s1", branch="main"):
    """A stand-in for a PyGithub WorkflowRun, as a run list returns it."""
    return SimpleNamespace(
        id=id_,
        name="CI",
        head_branch=branch,
        head_sha=sha,
        status=status,
        conclusion=conclusion,
        html_url=f"http://gh/run/{id_}",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _lister(runs):
    """A repo double whose `get_workflow_runs` records its filters; PR 12's head is `s1`."""
    seen = []

    def get_workflow_runs(**kw):
        seen.append(kw)
        return iter(runs)

    repo = SimpleNamespace(
        get_workflow_runs=get_workflow_runs,
        get_pull=lambda n: SimpleNamespace(head=SimpleNamespace(sha="s1")),
        get_workflow_job=lambda jid: SimpleNamespace(run_id=777),
    )
    provider = GitHubProvider(
        load_config(environ={}),
        client=SimpleNamespace(get_repo=lambda name: repo),
        environ={},
        repo="acme/app",
    )
    return provider, seen


def test_find_runs_normalises_status_and_asks_by_the_right_filter():
    """A run not yet completed is running; a PR is asked for by its head commit."""
    runs = [
        _wr(3, "in_progress", None),
        _wr(2, "completed", "failure"),
        _wr(1, "completed", "success"),
        _wr(0, "completed", "skipped"),
    ]
    provider, seen = _lister(runs)
    found = provider.find_runs(RunQuery(sha="s1", limit=3))
    assert [(r.id, r.status) for r in found] == [("3", "running"), ("2", "failed"), ("1", "success")]
    assert found[0].created_at == "2026-01-01T00:00:00+00:00"
    provider.find_runs(RunQuery(mr="12"))
    provider.find_runs(RunQuery(branch="dev"))
    assert seen == [{"head_sha": "s1"}, {"head_sha": "s1"}, {"branch": "dev"}]
    assert provider.find_runs(RunQuery())[0:0] == []  # nothing asked, nothing listed
    assert provider.find_runs(RunQuery(sha="s1", limit=4))[-1].status == "other"


def test_run_of_job_reads_the_jobs_run():
    """A check-run link names only the job; the API knows its run."""
    provider, _ = _lister([])
    assert provider.run_of_job("9") == "777"


def test_an_explicit_repo_wins_over_the_environment():
    """A caller that knows the repository is not second-guessed by GITHUB_REPOSITORY."""
    seen = []
    provider = GitHubProvider(
        load_config(environ={}),
        client=SimpleNamespace(get_repo=lambda name: seen.append(name)),
        environ={"GITHUB_REPOSITORY": "env/repo"},
        repo="given/repo",
    )
    provider._repo()
    assert seen == ["given/repo"]


def _log_job_provider(responses, monkeypatch):
    """A provider whose one job's log download answers `responses` in turn; sleeps recorded."""
    calls, slept = iter(responses), []
    monkeypatch.setattr("ci_doctor.providers.github.provider.requests.get", lambda *a, **k: next(calls))
    monkeypatch.setattr("ci_doctor.providers.github.provider.time.sleep", slept.append)
    provider = GitHubProvider(
        load_config(environ={}),
        client=_fake_client(
            [
                _job(
                    id=11,
                    name="b",
                    status="completed",
                    conclusion="failure",
                    logs_url=lambda: "https://logs/1",
                )
            ]
        ),
        environ={"GITHUB_REPOSITORY": "acme/app"},
    )
    return provider, provider.fetch_run("999").jobs[0], slept


def _resp(status, text="", **headers):
    """A requests.Response double."""
    error = requests.HTTPError(response=SimpleNamespace(status_code=status)) if status >= 400 else None

    def raise_for_status():
        if error is not None:
            raise error

    return SimpleNamespace(status_code=status, headers=headers, text=text, raise_for_status=raise_for_status)


def test_a_rate_limited_log_download_is_retried_once_after_the_wait_asked(monkeypatch):
    """429 with Retry-After: 2 waits two seconds, then the second answer is the log."""
    provider, job, slept = _log_job_provider(
        [_resp(429, **{"Retry-After": "2"}), _resp(200, "the log")], monkeypatch
    )
    assert provider.fetch_job_log(job) == "the log"
    assert slept == [2.0]


def test_a_second_rate_limit_is_reported_not_retried_again(monkeypatch):
    """One retry only: the second 429 becomes the recorded reason."""
    provider, job, slept = _log_job_provider([_resp(429), _resp(429)], monkeypatch)
    assert provider.fetch_job_log(job) is None
    assert slept == [1.0] and job.log_error is not None and job.log_error.startswith("HTTP 429")


def test_a_long_wait_is_not_slept(monkeypatch):
    """A Retry-After past the cap is reported at once: an interactive caller has a deadline."""
    provider, job, slept = _log_job_provider([_resp(429, **{"Retry-After": "600"})], monkeypatch)
    assert provider.fetch_job_log(job) is None
    assert slept == []
