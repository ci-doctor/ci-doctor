"""GitHub adapter: segmenter, reasons, provider mapping, attribution end to end.

The full run through the GitHub path proves core needed no changes for it.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job, MergeRequestRef, Phase
from ci_doctor.core.phases import assign_phases
from ci_doctor.core.ports import LogUnavailable
from ci_doctor.core.select import select_failed_jobs
from ci_doctor.providers.api_client import ApiError
from ci_doctor.providers.github.provider import GitHubProvider
from ci_doctor.providers.github.reasons import to_failure_reason
from ci_doctor.providers.github.segmenter import GitHubSegmenter
from tests.support import FakeApi, json_reply

MARKER = "<!-- ci-doctor:pipeline:999 -->"


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


REPO = "/repos/acme/app"
RUN = f"{REPO}/actions/runs/999"
BLOB = "https://blob.example.net/log?sig=secret"


def _gh_job(**kw):
    """A workflow job as the API returns it, with fields overridden by kwargs."""
    base = {"id": 0, "name": "", "status": "completed", "conclusion": None, "runner_name": None}
    return {**base, "runner_id": None, "started_at": None, "completed_at": None, "html_url": "", **kw}


def _run_routes(jobs, **run):
    """The two calls `fetch_run` makes: the run, then its jobs."""
    body = {"id": 999, "head_branch": "main", "head_sha": "deadbeef", "html_url": "http://gh/run/999"}
    return {
        ("GET", RUN): json_reply({**body, "pull_requests": [], **run}),
        ("GET", f"{RUN}/jobs"): json_reply({"total_count": len(jobs), "jobs": jobs}),
    }


def _github(routes, environ=None):
    """A GitHubProvider on a FakeApi serving `routes`. Returns both."""
    api = FakeApi(routes)
    env = {"GITHUB_REPOSITORY": "acme/app"} if environ is None else environ
    return GitHubProvider(load_config(environ={}), env, transport=api.transport), api


def test_provider_maps_jobs_and_normalizes_status():
    """Jobs map to the domain model, and failing conclusions become "failed"."""
    jobs = [
        _gh_job(
            id=11,
            name="build",
            conclusion="failure",
            runner_name="gh-runner-1",
            runner_id=7,
            html_url="http://gh/11",
            started_at="2024-01-01T00:00:00Z",
        ),
        _gh_job(id=12, name="flaky", conclusion="timed_out"),
        _gh_job(id=13, name="ok", conclusion="success"),
        _gh_job(id=14, name="slow", conclusion="cancelled"),
    ]
    provider, _ = _github(
        _run_routes(jobs), {"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/pull/42/merge"}
    )
    run = provider.fetch_run("999")

    build = run.jobs[0]
    assert build.status == "failed"  # normalized from conclusion
    assert build.failure_reason == FailureReason.SCRIPT_FAILURE
    assert build.runner.description == "gh-runner-1" and build.runner.id == "7"
    assert build.started_at == "2024-01-01T00:00:00Z"  # kept as the API sends it
    assert run.jobs[1].failure_reason == FailureReason.TIMEOUT
    assert run.jobs[2].status == "completed"  # success conclusion stays non-failed
    # A cancellation keeps its own status rather than being flattened into
    # "failed" — the domain has a word for it, and GitLab reports the same one.
    assert run.jobs[3].status == "cancelled"
    assert run.jobs[3].failure_reason == FailureReason.CANCELLED
    assert [j.name for j in select_failed_jobs(run.jobs)] == ["build", "flaky", "slow"]
    assert run.ref == "main" and run.sha == "deadbeef"  # straight off the run, no env vars
    assert run.mr is not None and run.mr.iid == "42"  # PR resolved from GITHUB_REF


def test_a_runs_jobs_are_read_across_every_page():
    """Jobs on later pages are read by following `Link`."""
    second = "https://api.github.com/repositories/1/actions/runs/999/jobs?page=2"
    routes = _run_routes([])
    routes[("GET", f"{RUN}/jobs")] = json_reply(
        {"jobs": [_gh_job(id=1, name="a")]}, link=f'<{second}>; rel="next"'
    )
    routes[("GET", "/repositories/1/actions/runs/999/jobs")] = json_reply({"jobs": [_gh_job(id=2, name="b")]})
    provider, _ = _github(routes)
    assert [j.name for j in provider.fetch_run("999").jobs] == ["a", "b"]


def test_requests_carry_the_token_and_githubs_headers():
    """The token goes in `Authorization`, with GitHub's API headers."""
    provider, api = _github(
        _run_routes([]), {"GITHUB_REPOSITORY": "acme/app", "CI_DOCTOR_GITHUB_TOKEN": "ghp_t"}
    )
    provider.fetch_run("999")
    headers = api.sent("GET", RUN)[0].headers
    assert headers["authorization"] == "Bearer ghp_t"
    assert headers["x-github-api-version"] and headers["accept"] == "application/vnd.github+json"


def test_a_missing_run_is_an_error_naming_github():
    """A missing run raises an error with GitHub's message."""
    provider, _ = _github({})
    with pytest.raises(ApiError, match="GitHub API: HTTP 404: Not Found"):
        provider.fetch_run("999")


def test_pull_request_on_the_run_wins_over_the_ref():
    """The run's own PR list is authoritative; GITHUB_REF is only the fork fallback."""
    provider, _ = _github(
        _run_routes([], pull_requests=[{"number": 7}]),
        {"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/pull/42/merge"},
    )
    assert provider.fetch_run("999").mr.iid == "7"


def test_repository_falls_back_to_git_origin(monkeypatch):
    """With no GITHUB_REPOSITORY, the local origin names the repo (and warns)."""
    from ci_doctor.providers import git_origin

    git_origin.origin_repo.cache_clear()
    monkeypatch.setattr(
        git_origin.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="git@github.com:acme/app.git\n")
    )
    provider, _ = _github({}, environ={})
    assert provider._repo() == REPO
    git_origin.origin_repo.cache_clear()


def test_origin_keeps_nested_gitlab_groups(monkeypatch):
    """A GitLab subgroup path survives; a bare regex on the tail would lose it."""
    from ci_doctor.providers import git_origin

    git_origin.origin_repo.cache_clear()
    monkeypatch.setattr(
        git_origin.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="https://gitlab.example.com/group/sub/project.git\n"),
    )
    assert git_origin.origin_repo("CI_PROJECT_ID") == "group/sub/project"
    git_origin.origin_repo.cache_clear()


REDIRECT = httpx.Response(302, headers={"location": BLOB})


def test_a_log_is_read_from_the_redirect_target_without_the_token():
    """The log is read from the redirect target, without the token."""
    provider, api = _github(
        {
            ("GET", f"{REPO}/actions/jobs/11/logs"): REDIRECT,
            ("GET", "/log"): httpx.Response(200, text="boom\n"),
        },
        {"GITHUB_REPOSITORY": "acme/app", "CI_DOCTOR_GITHUB_TOKEN": "ghp_t"},
    )
    assert provider.fetch_job_log(Job(id="11", name="build", status="failed")) == "boom\n"
    blob = api.sent("GET", "/log")[0]
    assert blob.url.host == "blob.example.net" and "authorization" not in blob.headers


@pytest.mark.parametrize("status", [404, 410])
def test_a_missing_or_expired_log_is_none(status):
    """A job that never started wrote no log, and an old one expired: both are valid data, not errors."""
    gone = httpx.Response(status)
    for log_route, blob in ((gone, None), (REDIRECT, gone), (REDIRECT, httpx.Response(200, text=""))):
        routes = {("GET", f"{REPO}/actions/jobs/11/logs"): log_route}
        if blob is not None:
            routes[("GET", "/log")] = blob
        provider, _ = _github(routes)
        assert provider.fetch_job_log(Job(id="11", name="build", status="failed")) is None


def test_a_refused_log_is_unavailable_not_missing():
    """A 403 says nothing about the job: reading it as "no log" made attribution blame a missing runner."""
    refused = json_reply({"message": "Must have admin rights to Repository."}, 403)
    provider, _ = _github({("GET", f"{REPO}/actions/jobs/11/logs"): refused})
    with pytest.raises(LogUnavailable, match=r"^HTTP 403: Must have admin rights to Repository\.$"):
        provider.fetch_job_log(Job(id="11", name="build", status="failed"))


def test_a_failed_blob_download_is_unavailable_and_never_names_the_signed_url():
    """The blob URL is short-lived and signed; it must not reach a report or an agent."""
    provider, _ = _github(
        {("GET", f"{REPO}/actions/jobs/11/logs"): REDIRECT, ("GET", "/log"): httpx.Response(500)}
    )
    with pytest.raises(LogUnavailable) as err:
        provider.fetch_job_log(Job(id="11", name="build", status="failed"))
    assert str(err.value) == "HTTP 500: Internal Server Error" and "secret" not in str(err.value)


def test_a_timeout_is_unavailable():
    """No response at all: the message is the error's type, since there is no status to give."""

    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    provider, _ = _github({("GET", f"{REPO}/actions/jobs/11/logs"): slow})
    with pytest.raises(LogUnavailable, match="^ReadTimeout$"):
        provider.fetch_job_log(Job(id="11", name="build", status="failed"))


def test_the_pr_comment_is_created_when_absent():
    """With no earlier report, one comment is posted with the marker."""
    comments = f"{REPO}/issues/7/comments"
    provider, api = _github(
        {("GET", comments): json_reply([]), ("POST", comments): json_reply({"id": 1}, 201)}
    )
    provider.post_note(MergeRequestRef(iid="7"), "the report", MARKER)
    [posted] = api.sent("POST", comments)
    assert json.loads(posted.content)["body"] == f"the report\n\n{MARKER}"


def test_the_pr_comment_is_edited_in_place_wherever_it_is_listed():
    """Our earlier comment is found by its marker, even on page 2, and edited."""
    comments = f"{REPO}/issues/7/comments"
    page_two = f"https://api.github.com{comments}?page=2"
    provider, api = _github(
        {
            ("GET", comments): [
                json_reply([{"id": 1, "body": "lgtm"}], link=f'<{page_two}>; rel="next"'),
                json_reply([{"id": 2, "body": f"old\n\n{MARKER}"}]),
            ],
            ("PATCH", f"{REPO}/issues/comments/2"): json_reply({"id": 2}),
        }
    )
    provider.post_note(MergeRequestRef(iid="7"), "new report", MARKER)
    [edited] = api.sent("PATCH", f"{REPO}/issues/comments/2")
    assert json.loads(edited.content)["body"].startswith("new report")
    assert api.sent("POST", comments) == []
