"""Listing runs: each provider's query and mapping, and the service's checks."""

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import RunEvent, RunFilter, RunStatus, RunSummary
from ci_doctor.errors import SetupError
from ci_doctor.providers.github.provider import GitHubProvider
from ci_doctor.providers.gitlab.provider import GitLabProvider
from ci_doctor.service import runs as service
from tests.support import FakeApi, json_reply

GH_RUNS = "/repos/acme/app/actions/runs"
GL_PIPELINES = "/api/v4/projects/42/pipelines"
SECRET = "glpat-" + "a" * 20


def _gh_run(**kw):
    """A workflow run as GitHub lists it, with fields overridden by kwargs."""
    base = {
        "id": 1,
        "status": "completed",
        "conclusion": "failure",
        "event": "push",
        "head_branch": "main",
        "head_sha": "deadbeefcafe",
        "display_title": "Fix the build\n\nlonger body",
        "actor": {"login": "ada"},
        "created_at": "2026-11-10T09:00:00Z",
        "html_url": "https://gh/run/1",
    }
    return {**base, **kw}


def _gl_pipeline(**kw):
    """A pipeline as GitLab lists it: no user and no commit title."""
    base = {
        "id": 7,
        "status": "failed",
        "source": "push",
        "ref": "main",
        "sha": "deadbeefcafe",
        "created_at": "2026-11-10T09:00:00Z",
        "web_url": "https://gl/pipe/7",
    }
    return {**base, **kw}


def _github(routes):
    api = FakeApi(routes)
    return GitHubProvider(
        load_config(environ={}), {"GITHUB_REPOSITORY": "acme/app"}, transport=api.transport
    ), api


def _gitlab(routes):
    api = FakeApi({("GET", "/api/v4/version"): json_reply({"version": "17.0"}), **routes})
    return GitLabProvider(load_config(environ={}), {"CI_PROJECT_ID": "42"}, transport=api.transport), api


# --- GitHub ---------------------------------------------------------------------


def test_github_turns_every_filter_into_its_query():
    """Neutral words go in, GitHub's come out: failed -> failure, manual -> workflow_dispatch."""
    provider, api = _github({("GET", GH_RUNS): json_reply({"workflow_runs": []})})
    run_filter = RunFilter(
        RunStatus.FAILED, "main", "abc", "ada", RunEvent.MANUAL, "2026-11-01", "2026-11-30", 5
    )
    assert provider.list_runs(run_filter) == []
    params = dict(api.requests[0].url.params)
    assert params == {
        "per_page": "5",
        "status": "failure",
        "event": "workflow_dispatch",
        "branch": "main",
        "head_sha": "abc",
        "actor": "ada",
        "created": "2026-11-01..2026-11-30",
    }


@pytest.mark.parametrize(
    ("since", "until", "created"),
    [("2026-11-01", None, ">=2026-11-01"), (None, "2026-11-30", "<=2026-11-30"), (None, None, None)],
)
def test_github_open_ended_date_ranges(since, until, created):
    """One end of the range is a `>=` or `<=`; neither sends no `created`."""
    provider, api = _github({("GET", GH_RUNS): json_reply({"workflow_runs": []})})
    provider.list_runs(RunFilter(since=since, until=until))
    assert api.requests[0].url.params.get("created") == created


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("completed", "failure", RunStatus.FAILED),
        ("completed", "timed_out", RunStatus.FAILED),
        ("completed", "success", RunStatus.SUCCESS),
        ("completed", "cancelled", RunStatus.CANCELLED),
        ("completed", "skipped", RunStatus.SKIPPED),
        ("completed", "stale", RunStatus.UNKNOWN),
        ("in_progress", None, RunStatus.RUNNING),
        ("queued", None, RunStatus.PENDING),
        ("waiting", None, RunStatus.PENDING),
    ],
)
def test_github_maps_each_state_onto_the_neutral_status(status, conclusion, expected):
    """A finished run reads its conclusion, an unfinished one its status; the raw word is kept."""
    run = _gh_run(status=status, conclusion=conclusion)
    provider, _ = _github({("GET", GH_RUNS): json_reply({"workflow_runs": [run]})})
    [summary] = provider.list_runs(RunFilter())
    assert summary.status == expected and summary.raw_status == (conclusion or status)


def test_github_summary_fields():
    """The title is the first line, the event neutral, the actor the login."""
    run = _gh_run(event="pull_request_target")
    provider, _ = _github({("GET", GH_RUNS): json_reply({"workflow_runs": [run]})})
    assert provider.list_runs(RunFilter()) == [
        RunSummary(
            id="1",
            status=RunStatus.FAILED,
            branch="main",
            sha="deadbeefcafe",
            event="pull_request",
            created_at="2026-11-10T09:00:00Z",
            web_url="https://gh/run/1",
            title="Fix the build",
            actor="ada",
            raw_status="failure",
        )
    ]


def test_the_limit_stops_paging_once_there_are_enough():
    """Three runs asked for, a full first page served: the second page is never fetched."""
    page = [_gh_run(id=i) for i in range(5)]
    next_page = f"https://api.github.com{GH_RUNS}?page=2"
    provider, api = _github(
        {("GET", GH_RUNS): json_reply({"workflow_runs": page}, link=f'<{next_page}>; rel="next"')}
    )
    assert [r.id for r in provider.list_runs(RunFilter(limit=3))] == ["0", "1", "2"]
    assert len(api.requests) == 1


# --- GitLab ---------------------------------------------------------------------


def test_gitlab_turns_every_filter_into_its_query():
    """Cancelled -> canceled, pull_request -> merge_request_event, dates -> created_after/before."""
    provider, api = _gitlab({("GET", GL_PIPELINES): json_reply([])})
    run_filter = RunFilter(
        RunStatus.CANCELLED, "main", "abc", "ada", RunEvent.PULL_REQUEST, "2026-11-01", "2026-11-30", 5
    )
    assert provider.list_runs(run_filter) == []
    [request] = api.sent("GET", GL_PIPELINES)
    assert dict(request.url.params) == {
        "per_page": "5",
        "status": "canceled",
        "source": "merge_request_event",
        "ref": "main",
        "sha": "abc",
        "username": "ada",
        "created_after": "2026-11-01",
        "created_before": "2026-11-30",
    }


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("failed", RunStatus.FAILED),
        ("success", RunStatus.SUCCESS),
        ("running", RunStatus.RUNNING),
        ("manual", RunStatus.PENDING),
        ("waiting_for_resource", RunStatus.PENDING),
        ("canceling", RunStatus.CANCELLED),
        ("skipped", RunStatus.SKIPPED),
        ("something_new", RunStatus.UNKNOWN),
    ],
)
def test_gitlab_maps_each_status_onto_the_neutral_status(status, expected):
    """Every waiting state reads as pending; an unknown one stays visible in raw_status."""
    provider, _ = _gitlab({("GET", GL_PIPELINES): json_reply([_gl_pipeline(status=status)])})
    [summary] = provider.list_runs(RunFilter())
    assert summary.status == expected and summary.raw_status == status


def test_gitlab_summary_has_no_title_or_actor_when_the_list_has_none():
    """GitLab's list carries neither; the summary says so instead of inventing one."""
    provider, _ = _gitlab({("GET", GL_PIPELINES): json_reply([_gl_pipeline(source="web")])})
    [summary] = provider.list_runs(RunFilter())
    assert (summary.id, summary.event, summary.title, summary.actor) == ("7", "manual", None, None)


# --- the service ----------------------------------------------------------------


class _Fake:
    """A CI provider that hands back fixed summaries and remembers the filter."""

    def __init__(self, runs=(), error=None):
        self.runs, self.error, self.filters = list(runs), error, []

    def list_runs(self, run_filter):
        self.filters.append(run_filter)
        if self.error:
            raise self.error
        return self.runs


def _summary(**kw):
    base = {
        "id": "1",
        "status": RunStatus.FAILED,
        "branch": "main",
        "sha": "deadbeefcafe",
        "event": "push",
        "created_at": "2026-11-10T09:00:00Z",
        "web_url": "https://ci/run/1",
        "title": "Fix the build",
    }
    return RunSummary(**{**base, **kw})


def test_a_secret_in_a_title_is_redacted(monkeypatch):
    """A commit title is free text from a person: it is scrubbed like any log line (invariant #5)."""
    fake = _Fake([_summary(title=f"rotate {SECRET}")])
    monkeypatch.setattr(service, "make_ci_provider", lambda cfg: fake)
    [run] = service.list_runs(load_config(environ={}), RunFilter())
    assert SECRET not in run.title and "[REDACTED:" in run.title


@pytest.mark.parametrize(
    ("run_filter", "message"),
    [
        (RunFilter(limit=0), "limit must be between 1 and 50"),
        (RunFilter(limit=51), "limit must be between 1 and 50"),
        (RunFilter(status=RunStatus.UNKNOWN), "status `unknown` only describes a run"),
        (RunFilter(since="last week"), "since must be an ISO 8601 date"),
        (RunFilter(until="2026-13-01"), "until must be an ISO 8601 date"),
    ],
)
def test_a_filter_the_ci_would_misread_is_refused_before_any_call(monkeypatch, run_filter, message):
    """A bad limit, status or date fails with the fix, and the provider is never asked."""
    fake = _Fake()
    monkeypatch.setattr(service, "make_ci_provider", lambda cfg: fake)
    with pytest.raises(SetupError, match=message):
        service.list_runs(load_config(environ={}), run_filter)
    assert fake.filters == []


def test_a_date_with_a_time_is_accepted(monkeypatch):
    """ISO 8601 with a time and zone passes through untouched."""
    fake = _Fake()
    monkeypatch.setattr(service, "make_ci_provider", lambda cfg: fake)
    service.list_runs(load_config(environ={}), RunFilter(since="2026-11-01T08:00:00+00:00"))
    assert fake.filters[0].since == "2026-11-01T08:00:00+00:00"


def test_an_api_failure_becomes_a_message_not_a_traceback(monkeypatch):
    """A refused token or an unknown project says what went wrong."""
    from ci_doctor.providers.api_client import ApiError

    monkeypatch.setattr(
        service, "make_ci_provider", lambda cfg: _Fake(error=ApiError("GitHub", 401, "Bad credentials"))
    )
    with pytest.raises(SetupError, match="could not list runs: GitHub API: HTTP 401: Bad credentials"):
        service.list_runs(load_config(environ={}), RunFilter())
