"""GitHub Actions adapter over GitHub's REST API.

Calls five endpoints (the run list, a run, its jobs, a job's log, PR comments) through
`api_client` and maps the JSON onto the domain model, the same one GitLab produces.
GitHub Enterprise works through `github.base_url`.
"""

import logging
import os
import re
from collections.abc import Mapping
from itertools import islice
from typing import Any
from urllib.parse import quote

import httpx

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import (
    Job,
    MergeRequestRef,
    Run,
    RunEvent,
    RunFilter,
    RunnerInfo,
    RunStatus,
    RunSummary,
)
from ci_doctor.core.ports import CIProvider, SCMProvider
from ci_doctor.errors import LogUnavailable
from ci_doctor.providers.api_client import ApiClient, ApiError, tls_verify
from ci_doctor.providers.git_origin import origin_repo
from ci_doctor.providers.github.reasons import to_failure_reason
from ci_doctor.providers.tokens import read_token

log = logging.getLogger("ci_doctor.github")

#: No log: it expired (410), or the job never started (404).
_NO_LOG = frozenset({404, 410})

#: Headers GitHub asks REST clients to send.
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

#: Job conclusions -> the domain's normalised status, so core job-selection needs
#: no GitHub knowledge. A timeout and a startup failure are failures; a
#: cancellation stays its own status, because that is what it is on every CI.
_CONCLUSION_STATUS = {
    "failure": "failed",
    "timed_out": "failed",
    "startup_failure": "failed",
    "cancelled": "cancelled",
}


#: A filter's status -> GitHub's `status` query value. GitHub takes one value, so
#: `failed` asks for `failure`, which is how almost every failed run concludes; a run
#: whose own conclusion is `timed_out` is listed under no status filter.
_STATUS_QUERY = {
    RunStatus.FAILED: "failure",
    RunStatus.SUCCESS: "success",
    RunStatus.RUNNING: "in_progress",
    RunStatus.PENDING: "queued",
    RunStatus.CANCELLED: "cancelled",
    RunStatus.SKIPPED: "skipped",
}
#: A finished run's conclusion -> the neutral status.
_RUN_CONCLUSION = {
    "failure": RunStatus.FAILED,
    "timed_out": RunStatus.FAILED,
    "startup_failure": RunStatus.FAILED,
    "success": RunStatus.SUCCESS,
    "cancelled": RunStatus.CANCELLED,
    "skipped": RunStatus.SKIPPED,
    "neutral": RunStatus.SKIPPED,
    "action_required": RunStatus.PENDING,
}
#: An unfinished run's status -> the neutral status.
_RUN_STATE = {"in_progress": RunStatus.RUNNING}
#: The neutral event -> GitHub's `event` value, and back.
_EVENT_QUERY = {
    RunEvent.PUSH: "push",
    RunEvent.PULL_REQUEST: "pull_request",
    RunEvent.SCHEDULE: "schedule",
    RunEvent.MANUAL: "workflow_dispatch",
}
_EVENT = {**{v: k for k, v in _EVENT_QUERY.items()}, "pull_request_target": RunEvent.PULL_REQUEST}


class GitHubProvider(CIProvider, SCMProvider):
    """Read-only GitHub Actions adapter.

    Serves both roles: workflow runs and pull requests are the same API behind
    the same client, so one instance answers as CI system and as git host.
    """

    def __init__(
        self,
        config: Config,
        environ: Mapping[str, str] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Set up the API client. Nothing is sent until the first call.

        Args:
            config: The effective config; only `config.github` is read.
            environ: Where the token, CA variables and GITHUB_* vars come from. Defaults to os.environ.
            transport: Replaces the network, for tests.

        Raises:
            ValueError: If `github.base_url` is empty or the CA bundle cannot be read.
        """
        self.cfg = config.github
        self.environ = os.environ if environ is None else environ
        if not self.cfg.base_url:
            raise ValueError("github.base_url must not be empty")
        token = read_token(self.cfg.token_file, self.cfg.token_env, self.environ)
        self.api = ApiClient(
            "GitHub",
            self.cfg.base_url,
            auth={"Authorization": f"Bearer {token}"} if token else {},
            headers=_HEADERS,
            verify=tls_verify(self.cfg.ca_bundle, self.cfg.verify_ssl, self.environ),
            timeout=self.cfg.timeout_seconds,
            limits=self.cfg.limits,
            transport=transport,
        )
        self._repo_path: str | None = None

    def _repo(self) -> str:
        """The repository's API path, from GITHUB_REPOSITORY or the git origin.

        Returns:
            `/repos/<owner>/<name>`.

        Raises:
            ValueError: If neither names a repository.
        """
        if self._repo_path is None:
            name = self.environ.get("GITHUB_REPOSITORY") or origin_repo("GITHUB_REPOSITORY")
            if not name:
                raise ValueError("GITHUB_REPOSITORY is not set and git origin gave no repository")
            self._repo_path = f"/repos/{quote(name, safe='/')}"
        return self._repo_path

    # --- CIProvider -------------------------------------------------------

    def fetch_run(self, run_ref: str) -> Run:
        """Fetch a workflow run and map its jobs.

        Args:
            run_ref: The workflow run id.

        Returns:
            The run. Job logs are not fetched here — that happens lazily, per
            selected job, so an unread job costs nothing.
        """
        path = f"{self._repo()}/actions/runs/{int(run_ref)}"
        run = self.api.get_json(path)
        jobs = [self._to_job(j) for j in self.api.paginate(f"{path}/jobs", key="jobs")]
        log.debug(f"run {run_ref}: {len(jobs)} jobs")
        return Run(
            id=str(run["id"]),
            ref=run.get("head_branch") or "",
            sha=run.get("head_sha") or "",
            web_url=run.get("html_url") or "",
            mr=self._pr_ref(run),
            jobs=jobs,
        )

    def fetch_job_log(self, job: Job) -> str | None:
        """Fetch one job's log.

        GitHub redirects to a short-lived blob URL, which the client follows
        without the token. An expired blob URL returns an error, not the log.

        Args:
            job: The job, with its GitHub id.

        Returns:
            The log, or None when there is none (empty, expired, or the job never started).

        Raises:
            LogUnavailable: If the log exists but could not be read (§7).
        """
        try:
            text = self.api.get_text(f"{self._repo()}/actions/jobs/{int(job.id)}/logs")
        except ApiError as err:
            if err.status in _NO_LOG:
                log.debug(f"job {job.id} has no log (HTTP {err.status})")
                return None
            raise LogUnavailable(err.detail) from None
        log.debug(f"job {job.id} log: {len(text)} chars")
        return text or None

    def list_runs(self, run_filter: RunFilter) -> list[RunSummary]:
        """List the repository's workflow runs, newest first.

        Args:
            run_filter: What to keep.

        Returns:
            At most `run_filter.limit` runs. Pages stop once there are enough.
        """
        query: dict[str, str | int] = {"per_page": min(run_filter.limit, 100)}
        if run_filter.status is not None:
            query["status"] = _STATUS_QUERY[run_filter.status]
        if run_filter.event is not None:
            query["event"] = _EVENT_QUERY[run_filter.event]
        for key, value in (
            ("branch", run_filter.branch),
            ("head_sha", run_filter.sha),
            ("actor", run_filter.actor),
        ):
            if value:
                query[key] = value
        if created := _created(run_filter.since, run_filter.until):
            query["created"] = created
        runs = self.api.paginate(f"{self._repo()}/actions/runs", key="workflow_runs", **query)
        return [self._to_summary(r) for r in islice(runs, run_filter.limit)]

    @staticmethod
    def _to_summary(run: dict[str, Any]) -> RunSummary:
        """Map one workflow run onto a summary."""
        state, conclusion = run.get("status") or "", run.get("conclusion") or ""
        if state == "completed":
            status = _RUN_CONCLUSION.get(conclusion, RunStatus.UNKNOWN)
        else:
            status = _RUN_STATE.get(state, RunStatus.PENDING)
        event = run.get("event") or ""
        title = run.get("display_title") or run.get("name")
        return RunSummary(
            id=str(run["id"]),
            status=status,
            branch=run.get("head_branch") or "",
            sha=run.get("head_sha") or "",
            event=str(_EVENT.get(event, RunEvent.OTHER)),
            created_at=run.get("created_at") or "",
            web_url=run.get("html_url") or "",
            title=title.splitlines()[0] if title else None,
            actor=(run.get("actor") or {}).get("login"),
            raw_status=conclusion or state,
        )

    # --- SCMProvider ------------------------------------------------------

    def post_note(self, mr: MergeRequestRef, body: str, marker: str) -> None:
        """Post the report on a pull request, or update our earlier comment.

        Our comment is found by `marker`, so a rerun edits it instead of adding
        another. In the API, a PR's comments are its issue's comments.

        Args:
            mr: The pull request.
            body: The rendered Markdown report.
            marker: Hidden comment identifying our note, appended to the body.
        """
        comments = f"{self._repo()}/issues/{int(mr.iid)}/comments"
        full = f"{body}\n\n{marker}"
        for comment in self.api.paginate(comments):
            if marker in (comment.get("body") or ""):
                self.api.send_json("PATCH", f"{self._repo()}/issues/comments/{comment['id']}", {"body": full})
                return
        self.api.send_json("POST", comments, {"body": full})

    # --- mapping ----------------------------------------------------------

    def _pr_ref(self, run: dict[str, Any]) -> MergeRequestRef | None:
        """Resolve the pull request for a run.

        Args:
            run: The workflow run, as the API returned it.

        Returns:
            The PR, or None. `GITHUB_REF` is the fallback for fork PRs, where the
            run's `pull_requests` list is empty.
        """
        for pr in run.get("pull_requests") or []:
            return MergeRequestRef(iid=str(pr["number"]))
        m = re.match(r"refs/pull/(\d+)/", self.environ.get("GITHUB_REF", ""))
        return MergeRequestRef(iid=m.group(1)) if m else None

    def _to_job(self, j: dict[str, Any]) -> Job:
        """Map a workflow job onto the domain model.

        Args:
            j: A job, as the API returned it.

        Returns:
            The job, its conclusion mapped to the domain's status.
        """
        conclusion = j.get("conclusion")
        startup = j.get("status") == "startup_failure" or conclusion == "startup_failure"
        status = _CONCLUSION_STATUS.get(conclusion or "", j.get("status") or "")
        runner_name = j.get("runner_name")
        runner = (
            RunnerInfo(id=_str_or_none(j.get("runner_id")), description=runner_name) if runner_name else None
        )
        return Job(
            id=str(j["id"]),
            name=j.get("name") or "",
            status=status,
            stage=None,
            failure_reason=to_failure_reason(conclusion, startup_failure=startup),
            raw_failure_reason=conclusion or "",
            allow_failure=False,
            started_at=j.get("started_at"),
            finished_at=j.get("completed_at"),
            runner=runner,
            needs=[],
            web_url=j.get("html_url") or "",
            log=None,  # fetched lazily via fetch_job_log
            sections=[],
        )


def _str_or_none(value: object) -> str | None:
    """Stringify an optional id without turning None into "None"."""
    return None if value is None else str(value)


def _created(since: str | None, until: str | None) -> str | None:
    """GitHub's `created` range: `a..b`, `>=a` or `<=b`; None when neither end is set."""
    if since and until:
        return f"{since}..{until}"
    if since:
        return f">={since}"
    return f"<={until}" if until else None
