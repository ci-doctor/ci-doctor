"""GitLab adapter tests against an in-memory python-gitlab double (no network)."""

from types import SimpleNamespace

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import FailureReason, RunQuery
from ci_doctor.core.select import select_failed_jobs
from ci_doctor.providers.gitlab.provider import GitLabProvider


def _pipeline_job(**kw):
    """Build a python-gitlab-shaped job double, overridden by kwargs."""
    base = {
        "id": 0,
        "name": "",
        "status": "failed",
        "stage": None,
        "allow_failure": False,
        "failure_reason": "",
        "started_at": None,
        "finished_at": None,
        "duration": None,
        "runner": None,
        "web_url": "",
        "needs": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_gl(pipeline_jobs, traces):
    """Build an in-memory stand-in for the python-gitlab client."""
    project = SimpleNamespace(
        pipelines=SimpleNamespace(
            get=lambda pid: SimpleNamespace(
                id=1,
                ref="main",
                sha="deadbeef",
                web_url="http://gl/pipe/1",
                jobs=SimpleNamespace(list=lambda all=True: pipeline_jobs),
            )
        ),
        jobs=SimpleNamespace(get=lambda jid: SimpleNamespace(trace=lambda: traces.get(jid, b""))),
    )
    return SimpleNamespace(projects=SimpleNamespace(get=lambda pid: project))


def _provider(pipeline_jobs, traces):
    """Build a GitLabProvider over the fake client, so nothing connects."""
    cfg = load_config(environ={})  # base_url is None; injected client => no _connect()
    gl = _fake_gl(pipeline_jobs, traces)
    return GitLabProvider(cfg, client=gl, environ={"CI_PROJECT_ID": "42"})


def test_fetch_run_maps_jobs():
    """Pipeline jobs map onto the domain model, runner and tags included."""
    jobs = [
        _pipeline_job(
            id=101,
            name="build",
            stage="build",
            failure_reason="script_failure",
            duration=3.2,
            runner={"id": 5, "description": "r5", "tag_list": ["docker"]},
            web_url="http://gl/j/101",
        ),
        _pipeline_job(id=102, name="lint", stage="test", allow_failure=True, failure_reason="script_failure"),
    ]
    run = _provider(jobs, {}).fetch_run("1")

    assert run.sha == "deadbeef"
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
    jobs = [_pipeline_job(id=1, name="x", status="canceled", failure_reason="")]
    run = _provider(jobs, {}).fetch_run("1")
    assert run.jobs[0].status == "cancelled"
    assert run.jobs[0].failure_reason == FailureReason.CANCELLED
    assert [j.name for j in select_failed_jobs(run.jobs)] == ["x"]


def test_fetch_job_log_and_empty_is_none():
    """A trace decodes to text, and an empty one becomes None, not ""."""
    jobs = [_pipeline_job(id=101, name="build"), _pipeline_job(id=102, name="stuck")]
    prov = _provider(jobs, {101: b"boom\nERROR: Job failed: exit code 1\n", 102: b""})
    run = prov.fetch_run("1")
    assert prov.fetch_job_log(run.jobs[0]).startswith("boom")
    assert prov.fetch_job_log(run.jobs[1]) is None  # never-got-a-runner case


def test_default_base_url_is_public_host():
    """base_url defaults to gitlab.com so the tool works with no config."""
    cfg = load_config(environ={})
    assert cfg.gitlab.base_url == "https://gitlab.com"


def test_blank_base_url_raises_on_connect():
    """An explicitly blank base_url fails loudly instead of silently guessing."""
    import pytest

    cfg = load_config(environ={}, overrides={"gitlab": {"base_url": ""}})
    with pytest.raises(ValueError, match="base_url"):
        GitLabProvider(cfg, environ={})  # no injected client -> _connect() runs


def test_a_failed_trace_download_is_recorded_not_mistaken_for_no_log():
    """An API error leaves `log` None but records why, so attribution won't say "no runner"."""

    class Refused(Exception):
        response_code = 401

    def trace():
        raise Refused("401: unauthorized")

    prov = _provider([_pipeline_job(id=101, name="build")], {})
    prov._project().jobs = SimpleNamespace(get=lambda jid: SimpleNamespace(trace=trace))
    job = prov.fetch_run("1").jobs[0]
    assert prov.fetch_job_log(job) is None
    assert job.log_error is not None and job.log_error.startswith("HTTP 401")


def _pipe(id_, status, sha="s1", ref="main"):
    """A python-gitlab pipeline double, as a list returns it."""
    return SimpleNamespace(
        id=id_,
        status=status,
        sha=sha,
        ref=ref,
        web_url=f"http://gl/p/{id_}",
        created_at="2026-01-01T00:00:00Z",
    )


def test_find_runs_normalises_status_and_asks_by_the_right_filter():
    """Waiting pipelines are running; an MR is asked through its own pipeline list."""
    seen = []
    pipes = [_pipe(3, "waiting_for_resource"), _pipe(2, "failed"), _pipe(1, "canceled"), _pipe(0, "manual")]

    def listing(**kw):
        seen.append(kw)
        return pipes

    prov = _provider([], {})
    project = prov._project()
    project.pipelines.list = listing
    project.mergerequests = SimpleNamespace(
        get=lambda iid: SimpleNamespace(pipelines=SimpleNamespace(list=listing))
    )
    found = prov.find_runs(RunQuery(sha="s1", limit=4))
    assert [(r.id, r.status) for r in found] == [
        ("3", "running"),
        ("2", "failed"),
        ("1", "cancelled"),
        ("0", "other"),
    ]
    prov.find_runs(RunQuery(branch="dev", limit=2))
    prov.find_runs(RunQuery(mr="5", limit=2))
    assert seen[0]["sha"] == "s1" and seen[1]["ref"] == "dev" and "sha" not in seen[2]
    assert len(prov.find_runs(RunQuery(mr="5", limit=2))) == 2


def test_run_of_job_reads_the_jobs_pipeline():
    """A job URL names no pipeline; the job does."""
    prov = _provider([], {})
    prov._project().jobs = SimpleNamespace(get=lambda jid: SimpleNamespace(pipeline={"id": 31}))
    assert prov.run_of_job("8") == "31"


def test_an_explicit_project_wins_over_the_environment():
    """A caller that knows the project is not second-guessed by CI_PROJECT_ID."""
    seen = []
    gl = SimpleNamespace(projects=SimpleNamespace(get=lambda pid: seen.append(pid)))
    GitLabProvider(load_config(environ={}), client=gl, environ={"CI_PROJECT_ID": "42"}, repo="g/p")._project()
    assert seen == ["g/p"]
