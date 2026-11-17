"""The analysis itself, in two stages: diagnose, then explain.

Diagnose is `segment -> attribute -> evidence -> brief`: deterministic, and it
calls no model. Explain turns a diagnosis into the report, through the configured
LLM or without one. `ci-doctor analyze` runs both, over a live run or a log file;
the MCP server runs only the first and hands the brief to the agent, which is the
model there (invariant #12).

Not in `core/`: choosing an adapter and reading a run is I/O, and `core/` stays
pure and provider-neutral (invariant #2). This is the layer that knows both.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple, TypeVar

from ci_doctor.config.schemas import Config
from ci_doctor.core.analyze import EvidenceBundle, build_bundle
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.models import FailureReason, Job, Run
from ci_doctor.core.ports import CIProvider, LLMClient, LogSegmenter
from ci_doctor.engine import Plan, Spec, compile_plan
from ci_doctor.errors import LogUnavailable
from ci_doctor.llm.schema import Report
from ci_doctor.providers.registry import make_ci_provider, make_segmenter, segmenter_for_log
from ci_doctor.service.brief import Brief, build_brief

log = logging.getLogger("ci_doctor.service.analyze")

_R = TypeVar("_R")


class Diagnosis(NamedTuple):
    """One diagnosed job: everything settled without a model.

    Attributes:
        job: The failed job, log and sections attached.
        attribution: The classifier's verdict. Owns the phase.
        bundle: The evidence behind the verdict. Not redacted.
        brief: What a model is shown of the three above, redacted. The one thing
            to hand on (invariant #5).
    """

    job: Job
    attribution: Attribution
    bundle: EvidenceBundle
    brief: Brief


class JobResult(NamedTuple):
    """One analyzed job: what failed, where, and what to tell the reader.

    Attributes:
        job: The failed job, log and sections attached.
        attribution: The classifier's verdict. Owns the phase; the LLM may not.
        report: The final report, validated and redacted.
        bundle: The evidence the report was written from. Not redacted: whoever
            hands it on scrubs it first (invariant #5).
    """

    job: Job
    attribution: Attribution
    report: Report
    bundle: EvidenceBundle


def diagnose_job(job: Job, cfg: Config, plan: Plan, segmenter: LogSegmenter | None = None) -> Diagnosis:
    """Segment, classify and assemble the evidence and the brief for one job. No model is called.

    Args:
        job: The failed job, log already attached.
        cfg: The effective config.
        plan: The run's compiled matchers.
        segmenter: Parser for this log's format. Defaults to the one `config.ci`
            names, which is right for a live run — the provider that produced the
            log is the one we just read it from.

    Returns:
        The job, its verdict, its evidence and its brief.
    """
    from ci_doctor.core.attribution import attribute
    from ci_doctor.core.phases import assign_phases

    job.sections = (segmenter or make_segmenter(cfg)).segment(job.log or "")
    log.debug(f"job {job.name}: {len(job.sections)} top-level sections")
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)
    log.debug(
        f"job {job.name}: attribution phase={attr.phase} reason={attr.reason} rule={attr.rule_id} confidence={attr.confidence}",
    )
    bundle = build_bundle(job, attr, job.sections, cfg, plan)
    return Diagnosis(job, attr, bundle, build_brief(job, attr, bundle, cfg))


def explain(diagnosis: Diagnosis, cfg: Config, client: LLMClient | None = None) -> JobResult:
    """Produce the report for one diagnosed job.

    Deterministic when the LLM is disabled or unconfigured; one LLM call otherwise,
    its prompt rendered from the diagnosis's brief.

    Args:
        diagnosis: The diagnosed job.
        cfg: The effective config.
        client: The run's shared LLM client, from `llm.report.client_for_run`.
            None lets `produce_report` build its own, which is what a single-job
            caller wants.

    Returns:
        The job, its verdict, and its report.
    """
    from ci_doctor.llm.report import produce_report

    job, attr, bundle, brief = diagnosis
    report = produce_report(job, attr, bundle, cfg, brief, client=client)
    log.debug(
        f"job {job.name}: report category={report.category} confidence={report.confidence} infra={report.is_infra_not_code}",
    )
    return JobResult(job, attr, report, bundle)


def process_job(
    job: Job,
    cfg: Config,
    plan: Plan,
    segmenter: LogSegmenter | None = None,
    client: LLMClient | None = None,
) -> JobResult:
    """Diagnose one job, then explain it: `diagnose_job` and `explain` in one call.

    Args:
        job: The failed job, log already attached.
        cfg: The effective config.
        plan: The run's compiled matchers.
        segmenter: Parser for this log's format; see `diagnose_job`.
        client: The run's shared LLM client; see `explain`.

    Returns:
        The job, its verdict, and its report.
    """
    return explain(diagnose_job(job, cfg, plan, segmenter), cfg, client)


def _ordered(jobs: list[Job]) -> list[Job]:
    """Put failed jobs in the order the pipeline ran them.

    The provider returns jobs in API order, which is not pipeline order — GitLab
    hands back the newest first, so a report would recap `deploy` before `build`.
    Sort by `started_at` instead, which is the order a reader watched them run.
    A job that never started (no runner) has no timestamp and sorts first, where
    it belongs in the lifecycle; the sort is stable, so same-instant ties keep
    API order rather than shuffling.

    Args:
        jobs: The selected failed jobs.

    Returns:
        The same jobs, chronological.
    """
    return sorted(jobs, key=lambda j: (j.started_at is not None, j.started_at or ""))


def _fetch(run_id: str, cfg: Config, job_id: str | None) -> tuple[Run, CIProvider, list[Job]]:
    """Fetch a run and the logs of its failed jobs.

    Args:
        run_id: The provider's run/pipeline id.
        cfg: The effective config.
        job_id: Restrict to a single job id, or None for all failed ones.

    Returns:
        A ``(run, provider, jobs)`` tuple, the jobs in pipeline order with logs attached.
    """
    from ci_doctor.core.select import select_failed_jobs

    provider = make_ci_provider(cfg)
    run = provider.fetch_run(run_id)
    jobs = select_failed_jobs(run.jobs, cfg.analysis.include_allowed_failures)
    if job_id is not None:
        jobs = [j for j in jobs if j.id == job_id]
    log.debug(f"run {run_id}: {len(run.jobs)} jobs, {len(jobs)} failed and selected")

    selected = _ordered(jobs)[: cfg.analysis.max_jobs_analyzed]
    log.info(f"analyzing {len(selected)} failed job(s) from run {run_id}")
    # ponytail: one log at a time, to stay well under rate limits. The client is
    # thread-safe, so these fetches can run in parallel if this gets slow.
    for job in selected:
        try:
            job.log = provider.fetch_job_log(job)
        except LogUnavailable as exc:
            # Say so on the job: attribution must not read an unreadable log as an empty one.
            log.warning(f"could not read the log of job {job.id}: {exc}")
            job.log_error = str(exc)
    return run, provider, selected


def diagnose_run(
    run_id: str, cfg: Config, matchers: list[Spec], job_id: str | None = None
) -> list[Diagnosis]:
    """Fetch a run from the provider and diagnose its failed jobs. No model is called.

    Args:
        run_id: The provider's run/pipeline id.
        cfg: The effective config.
        matchers: The evidence matchers, loaded from the configured registries.
        job_id: Restrict to a single job id, or None for all failed ones.

    Returns:
        One diagnosis per failed job, in pipeline order; empty when none failed.
    """
    _, _, jobs = _fetch(run_id, cfg, job_id)
    plan = compile_plan(matchers, cfg.matchers.exclude)
    return _each(jobs, cfg, lambda job: diagnose_job(job, cfg, plan))


def analyze_run(
    run_id: str, cfg: Config, matchers: list[Spec], job_id: str | None = None
) -> tuple[Run, CIProvider, list[JobResult]]:
    """Fetch a run from the provider and analyze its failed jobs.

    Args:
        run_id: The provider's run/pipeline id.
        cfg: The effective config.
        matchers: The evidence matchers, loaded from the configured registries.
        job_id: Restrict to a single job id, or None for all failed ones.

    Returns:
        A ``(run, provider, results)`` tuple. The provider is returned so the
        caller can post an MR note without reconnecting.
    """
    run, provider, jobs = _fetch(run_id, cfg, job_id)
    return run, provider, _analyze(jobs, cfg, matchers)


def _each(jobs: list[Job], cfg: Config, step: Callable[[Job], _R]) -> list[_R]:
    """Run one step over every job, concurrently when the config allows it.

    Order is preserved regardless of which job finishes first — the reader expects
    pipeline order, not completion order.

    Args:
        jobs: The selected jobs, logs already attached.
        cfg: The effective config; `analysis.max_parallel_jobs` sets the width.
        step: What to do with one job.

    Returns:
        One result per job, in the order given.
    """
    workers = min(cfg.analysis.max_parallel_jobs, len(jobs))
    if workers <= 1:
        return [step(job) for job in jobs]
    log.debug(f"analyzing {len(jobs)} job(s) across {workers} workers")
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ci-doctor") as pool:
        # `map` yields in submission order, so the report reads chronologically even
        # though the jobs finished in whatever order the endpoint answered them.
        return list(pool.map(step, jobs))


def _analyze(
    jobs: list[Job], cfg: Config, matchers: list[Spec], segmenter: LogSegmenter | None = None
) -> list[JobResult]:
    """Diagnose and explain every job, sharing one LLM client.

    The jobs are independent and the LLM call dominates each one, so this is where
    a run's wall-clock time actually goes.

    Args:
        jobs: The selected jobs, logs already attached.
        cfg: The effective config.
        matchers: The evidence matchers, loaded from the configured registries.
        segmenter: Parser for the log format, or None to derive it per job.

    Returns:
        One result per job, in the order given.
    """
    from ci_doctor.llm.report import client_for_run

    plan = compile_plan(matchers, cfg.matchers.exclude)
    client = client_for_run(cfg)
    try:
        return _each(jobs, cfg, lambda job: process_job(job, cfg, plan, segmenter, client))
    finally:
        if client is not None:
            client.close()


def analyze_log(path: Path, cfg: Config, matchers: list[Spec]) -> list[JobResult]:
    """Analyze a captured job log offline.

    Args:
        path: The log file.
        cfg: The effective config.
        matchers: The evidence matchers, loaded from the configured registries.

    Returns:
        One result per job in the synthetic run — today always exactly one.

    Raises:
        OSError: If the file cannot be read.
    """
    jobs = run_from_file(path).jobs
    log.info(f"analyzing {len(jobs)} job(s) from {path}")
    # The format comes from the log, not from `config.ci`: a replayed file has no
    # run metadata, so `ci` here is whatever the config defaults to.
    segmenter = segmenter_for_log(jobs[0].log or "", cfg.ci)
    return _analyze(jobs, cfg, matchers, segmenter)


def run_from_file(path: Path) -> Run:
    """Load a raw job log into the domain model for offline replay.

    Args:
        path: The log file.

    Returns:
        A synthetic single-job run. The failure reason is UNKNOWN — a bare log
        carries no provider metadata — which sends attribution down its
        structural fallback rules.

    Raises:
        OSError: If the file cannot be read.
    """
    # "replace": one non-UTF-8 byte from a tool must not cost the whole analysis, and it
    # keeps lone surrogates (which RE2 cannot encode) out of the pipeline entirely.
    raw = path.read_text(errors="replace")
    job = Job(id="local", name=path.stem, status="failed", failure_reason=FailureReason.UNKNOWN, log=raw)
    return Run(id="local", jobs=[job])
