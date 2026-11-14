"""GitLab adapter tests against an in-memory REST API (no network)."""

import logging
from types import SimpleNamespace

import httpx
import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import FailureReason, Job
from ci_doctor.core.ports import LogUnavailable
from ci_doctor.core.select import select_failed_jobs
from ci_doctor.providers.gitlab.provider import GitLabProvider
from tests.support import FakeApi, json_reply

API = "/api/v4"
PROJECT = f"{API}/projects/42"
PIPELINE = f"{PROJECT}/pipelines/1"
VERSION = json_reply({"version": "17.5.0", "revision": "abc"})


def _gl_job(**kw):
    """A pipeline job as the API returns it, with fields overridden by kwargs."""
    base = {
        "id": 0,
        "name": "",
        "status": "failed",
        "stage": None,
        "allow_failure": False,
        "failure_reason": "",
    }
    return {
        **base,
        "started_at": None,
        "finished_at": None,
        "duration": None,
        "runner": None,
        "web_url": "",
        **kw,
    }


def _gitlab(routes, environ=None):
    """A GitLabProvider on a FakeApi serving `routes` plus `/version`. Returns both."""
    api = FakeApi({("GET", f"{API}/version"): VERSION, **routes})
    env = {"CI_PROJECT_ID": "42"} if environ is None else environ
    return GitLabProvider(load_config(environ={}), env, transport=api.transport), api


def _pipeline_routes(jobs):
    """The two calls `fetch_run` makes: the pipeline, then its jobs."""
    pipeline = {"id": 1, "ref": "main", "sha": "deadbeef", "web_url": "http://gl/pipe/1"}
    return {("GET", PIPELINE): json_reply(pipeline), ("GET", f"{PIPELINE}/jobs"): json_reply(jobs)}


def test_fetch_run_maps_jobs():
    """Pipeline jobs map onto the domain model, runner and tags included."""
    jobs = [
        _gl_job(
            id=101,
            name="build",
            stage="build",
            failure_reason="script_failure",
            duration=3.2,
            runner={"id": 5, "description": "r5", "tag_list": ["docker"]},
            web_url="http://gl/j/101",
        ),
        _gl_job(id=102, name="lint", stage="test", allow_failure=True, failure_reason="script_failure"),
    ]
    provider, _ = _gitlab(_pipeline_routes(jobs))
    run = provider.fetch_run("1")

    assert run.sha == "deadbeef" and run.ref == "main"
    assert [j.name for j in run.jobs] == ["build", "lint"]
    build = run.jobs[0]
    assert build.failure_reason == FailureReason.SCRIPT_FAILURE
    assert build.raw_failure_reason == "script_failure"
    assert build.runner.id == "5" and build.runner.tags == ["docker"]
    assert run.jobs[1].allow_failure is True


def test_canceled_normalises_to_the_domain_spelling_and_is_analyzed():
    """GitLab's "canceled" becomes the domain's "cancelled", reason included.

    Both halves matter: `core.select` decides what to analyze from `status`, and
    it cannot know one CI spells it with a single l. Leaving the vendor spelling
    through is how a cancelled job got analyzed on GitHub and silently dropped
    on GitLab.
    """
    provider, _ = _gitlab(_pipeline_routes([_gl_job(id=1, name="x", status="canceled")]))
    run = provider.fetch_run("1")
    assert run.jobs[0].status == "cancelled"
    assert run.jobs[0].failure_reason == FailureReason.CANCELLED
    assert [j.name for j in select_failed_jobs(run.jobs)] == ["x"]


def test_a_pipelines_jobs_are_read_across_every_page():
    """Jobs on later pages are read by following `x-next-page`."""
    routes = _pipeline_routes([])
    routes[("GET", f"{PIPELINE}/jobs")] = [
        json_reply([_gl_job(id=1, name="a")], x_next_page="2"),
        json_reply([_gl_job(id=2, name="b")], x_next_page=""),
    ]
    provider, api = _gitlab(routes)
    assert [j.name for j in provider.fetch_run("1").jobs] == ["a", "b"]
    assert api.sent("GET", f"{PIPELINE}/jobs")[1].url.params["page"] == "2"


def test_requests_carry_the_token_in_gitlabs_header():
    """The token goes in the `PRIVATE-TOKEN` header."""
    provider, api = _gitlab(
        _pipeline_routes([]), {"CI_PROJECT_ID": "42", "CI_DOCTOR_GITLAB_TOKEN": "glpat-t"}
    )
    provider.fetch_run("1")
    assert api.sent("GET", PIPELINE)[0].headers["private-token"] == "glpat-t"


def test_project_falls_back_to_the_origin_path_url_encoded(monkeypatch):
    """Outside CI, the URL-encoded origin path is used as the project id."""
    from ci_doctor.providers import git_origin

    git_origin.origin_repo.cache_clear()
    monkeypatch.setattr(
        git_origin.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout="git@gitlab.com:acme/platform/api.git\n"),
    )
    provider, _ = _gitlab({}, environ={})
    assert provider._project() == "/projects/acme%2Fplatform%2Fapi"
    git_origin.origin_repo.cache_clear()


def test_the_instance_version_is_logged_and_a_gated_probe_is_not_fatal(caplog):
    """The version is logged; if the endpoint refuses, a warning is logged and setup continues."""
    with caplog.at_level(logging.INFO, logger="ci_doctor.gitlab"):
        _gitlab({})
    assert "connected to GitLab 17.5.0 (abc)" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="ci_doctor.gitlab"):
        _gitlab({("GET", f"{API}/version"): json_reply({"message": "401 Unauthorized"}, 401)})
    assert "could not detect GitLab version (continuing): GitLab API: HTTP 401" in caplog.text


def test_fetch_job_log_and_empty_is_none():
    """A trace decodes to text, and an empty one becomes None, not ""."""
    provider, _ = _gitlab(
        {
            ("GET", f"{PROJECT}/jobs/101/trace"): httpx.Response(
                200, text="boom\nERROR: Job failed: exit code 1\n"
            ),
            ("GET", f"{PROJECT}/jobs/102/trace"): httpx.Response(200, text=""),
        }
    )
    assert provider.fetch_job_log(Job(id="101", name="build", status="failed")).startswith("boom")
    assert provider.fetch_job_log(Job(id="102", name="stuck", status="failed")) is None  # never got a runner


def test_a_trace_gitlab_does_not_have_is_none():
    """A 404 is a job that wrote no trace (or whose trace expired): valid data, like an empty one."""
    provider, _ = _gitlab({})
    assert provider.fetch_job_log(Job(id="1", name="x", status="failed")) is None


def _timeout(request):
    raise httpx.ConnectTimeout("timed out", request=request)


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (json_reply({"message": "403 Forbidden"}, 403), "HTTP 403: Forbidden"),
        (httpx.Response(500), "HTTP 500: Internal Server Error"),
        (_timeout, "ConnectTimeout"),
    ],
)
def test_a_trace_that_could_not_be_read_is_unavailable_not_missing(reply, message):
    """Access denied or an outage says nothing about the job; it must not read as "never got a runner"."""
    provider, _ = _gitlab({("GET", f"{PROJECT}/jobs/1/trace"): reply})
    with pytest.raises(LogUnavailable) as err:
        provider.fetch_job_log(Job(id="1", name="x", status="failed"))
    assert str(err.value) == message


def test_default_base_url_is_public_host():
    """base_url defaults to gitlab.com so the tool works with no config."""
    cfg = load_config(environ={})
    assert cfg.gitlab.base_url == "https://gitlab.com"


def test_blank_base_url_raises_on_connect():
    """An explicitly blank base_url fails loudly instead of silently guessing."""
    cfg = load_config(environ={}, overrides={"gitlab": {"base_url": ""}})
    with pytest.raises(ValueError, match="base_url"):
        GitLabProvider(cfg, environ={})
