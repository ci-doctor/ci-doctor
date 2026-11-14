"""GitLab CI adapter over GitLab's REST API.

Calls four endpoints (the pipeline, its jobs, a job's trace, MR notes) through
`api_client` and maps the JSON onto the domain model. Works with any instance
through `gitlab.base_url`. Fields are read with defaults, because older instances
omit some.
"""

import logging
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import FailureReason, Job, MergeRequestRef, Run, RunnerInfo
from ci_doctor.core.ports import CIProvider, LogUnavailable, SCMProvider
from ci_doctor.providers.api_client import ApiClient, ApiError, tls_verify
from ci_doctor.providers.git_origin import origin_repo
from ci_doctor.providers.gitlab.reasons import to_failure_reason
from ci_doctor.providers.tokens import read_token

log = logging.getLogger("ci_doctor.gitlab")


class GitLabProvider(CIProvider, SCMProvider):
    """Read-only GitLab adapter.

    Serves both roles: pipelines and merge requests are the same API behind the
    same client, so one instance answers as CI system and as git host. A Jenkins
    build of a GitLab repo uses only the SCM half.
    """

    def __init__(
        self,
        config: Config,
        environ: Mapping[str, str] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Set up the API client and log the instance's version.

        Args:
            config: The effective config; only `config.gitlab` is read.
            environ: Where the token, CA variables and CI_* vars come from. Defaults to os.environ.
            transport: Replaces the network, for tests.

        Raises:
            ValueError: If `gitlab.base_url` is empty or the CA bundle cannot be read.
        """
        self.cfg = config.gitlab
        self.environ = os.environ if environ is None else environ
        if not self.cfg.base_url:
            raise ValueError("gitlab.base_url must not be empty")
        token = read_token(self.cfg.token_file, self.cfg.token_env, self.environ)
        self.api = ApiClient(
            "GitLab",
            f"{self.cfg.base_url.rstrip('/')}/api/v{self.cfg.api_version.lstrip('v')}",
            auth={"PRIVATE-TOKEN": token} if token else {},
            verify=tls_verify(self.cfg.ca_bundle, self.cfg.verify_ssl, self.environ),
            timeout=self.cfg.timeout_seconds,
            transport=transport,
        )
        self._project_path: str | None = None
        self._log_version()

    def _log_version(self) -> None:
        """Log the GitLab version. Some instances need a token for it, so a refusal only warns."""
        try:
            about = self.api.get_json("/version")
            log.info(
                f"connected to GitLab {about.get('version')} ({about.get('revision')}) at {self.cfg.base_url}"
            )
        except ApiError as err:
            log.warning(f"could not detect GitLab version (continuing): {err}")

    def _project(self) -> str:
        """The project's API path, from CI_PROJECT_ID or the git origin.

        Returns:
            `/projects/<id>`. Outside CI the id is the URL-encoded `group/project`
            path, which GitLab accepts in place of the number.

        Raises:
            ValueError: If neither names a project.
        """
        if self._project_path is None:
            pid = self.environ.get("CI_PROJECT_ID") or origin_repo("CI_PROJECT_ID")
            if not pid:
                raise ValueError("CI_PROJECT_ID is not set and git origin gave no project")
            self._project_path = f"/projects/{quote(pid, safe='')}"
        return self._project_path

    # --- CIProvider -------------------------------------------------------

    def fetch_run(self, run_ref: str) -> Run:
        """Fetch a pipeline and map its jobs.

        Args:
            run_ref: The pipeline id.

        Returns:
            The run. Job logs are not fetched here — that happens lazily, per
            selected job, so an unread job costs nothing.
        """
        path = f"{self._project()}/pipelines/{int(run_ref)}"
        pipeline = self.api.get_json(path)
        jobs = [self._to_job(j) for j in self.api.paginate(f"{path}/jobs")]
        log.debug(f"pipeline {run_ref}: {len(jobs)} jobs")
        return Run(
            id=str(pipeline.get("id", run_ref)),
            ref=pipeline.get("ref") or "",
            sha=pipeline.get("sha") or "",
            web_url=pipeline.get("web_url") or "",
            mr=self._merge_request_ref(),
            jobs=jobs,
        )

    def _merge_request_ref(self) -> MergeRequestRef | None:
        """Resolve the MR from the predefined CI variable.

        Returns:
            The MR reference, or None outside an MR pipeline. Read from
            `CI_MERGE_REQUEST_IID` rather than guessed from branch or SHA.
        """
        iid = self.environ.get("CI_MERGE_REQUEST_IID")
        if not iid:
            return None
        return MergeRequestRef(iid=iid, project_id=self.environ.get("CI_PROJECT_ID"))

    def fetch_job_log(self, job: Job) -> str | None:
        """Fetch one job's trace.

        Args:
            job: The job, with its GitLab id.

        Returns:
            The trace, or None when there is none (empty, expired, or the job never started).

        Raises:
            LogUnavailable: If the trace exists but could not be read (§7).
        """
        try:
            text = self.api.get_text(f"{self._project()}/jobs/{int(job.id)}/trace")
        except ApiError as err:
            if err.status == 404:
                log.debug(f"job {job.id} has no trace (HTTP 404)")
                return None
            raise LogUnavailable(err.detail) from None
        log.debug(f"job {job.id} trace: {len(text)} chars")
        return text or None

    # --- SCMProvider ------------------------------------------------------

    def post_note(self, mr: MergeRequestRef, body: str, marker: str) -> None:
        """Post the report on a merge request, or update our earlier note.

        Our note is found by `marker`, so a rerun edits it instead of adding another.

        Args:
            mr: The merge request.
            body: The rendered Markdown report.
            marker: Hidden comment identifying our note, appended to the body.
        """
        notes = f"{self._project()}/merge_requests/{int(mr.iid)}/notes"
        full = f"{body}\n\n{marker}"
        for note in self.api.paginate(notes):
            if marker in (note.get("body") or ""):
                self.api.send_json("PUT", f"{notes}/{note['id']}", {"body": full})
                return
        self.api.send_json("POST", notes, {"body": full})

    # --- mapping ----------------------------------------------------------

    def _to_job(self, j: dict[str, Any]) -> Job:
        """Map a pipeline job onto the domain model.

        Args:
            j: A job, as the API returned it.

        Returns:
            The job. Every field but the id has a default, because older instances omit some.
        """
        raw_status = j.get("status") or ""
        raw_reason = j.get("failure_reason") or ""
        cancelled = raw_status == "canceled"  # GitLab spells it with one l
        reason = FailureReason.CANCELLED if cancelled else to_failure_reason(raw_reason)
        return Job(
            id=str(j["id"]),
            name=j.get("name") or "",
            status="cancelled" if cancelled else raw_status,
            stage=j.get("stage"),
            failure_reason=reason,
            raw_failure_reason=raw_reason,
            allow_failure=bool(j.get("allow_failure", False)),
            started_at=j.get("started_at"),
            finished_at=j.get("finished_at"),
            duration=j.get("duration"),
            runner=self._to_runner(j.get("runner")),
            needs=self._to_needs(j.get("needs")),
            web_url=j.get("web_url") or "",
            log=None,  # fetched lazily via fetch_job_log
            sections=[],
        )

    @staticmethod
    def _to_runner(runner: dict[str, Any] | None) -> RunnerInfo | None:
        """Map GitLab's runner object onto the domain model.

        Args:
            runner: The runner, or None when no runner was assigned.

        Returns:
            The runner info, or None.
        """
        if not runner:
            return None
        rid = runner.get("id")
        return RunnerInfo(
            id=str(rid) if rid is not None else None,
            description=runner.get("description"),
            tags=runner.get("tag_list") or [],
        )

    @staticmethod
    def _to_needs(needs: list[dict[str, Any] | str] | None) -> list[str]:
        """Extract upstream job names from GitLab's `needs`.

        Args:
            needs: A list of objects or plain strings, or None.

        Returns:
            Upstream job names, used to spot cascade failures.
        """
        out = []
        for n in needs or []:
            name = n.get("name") if isinstance(n, dict) else n
            if name:
                out.append(name)
        return out
