"""The analysis itself: `segment -> attribute -> evidence -> report`.

Over a live run or a log file on disk, behind `ci-doctor analyze` and anything
else that drives ci-doctor programmatically.

Not in `core/`: choosing an adapter and reading a run is I/O, and `core/` stays
pure and provider-neutral (invariant #2). This is the layer that knows both.
"""

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

from ci_doctor.config.schemas import Config
from ci_doctor.core.analyze import EvidenceBundle
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.models import FailureReason, Job, Run
from ci_doctor.core.ports import CIProvider, LLMClient, LogSegmenter
from ci_doctor.engine import Plan, Spec, compile_plan
from ci_doctor.llm.schema import Report
from ci_doctor.providers.registry import make_ci_provider, make_segmenter, segmenter_for_log

log = logging.getLogger("ci_doctor.service.analyze")

#: Called as the analysis moves: ``(stage, done, total)``. Stages are "log" (one
#: job's log fetched) and "job" (one job analyzed). Called from worker threads
#: when jobs run in parallel; `done` still counts up by one each time.
Progress = Callable[[str, int, int], None]

#: Asked between steps, never inside one: True stops the analysis with :class:`Cancelled`.
CancelCheck = Callable[[], bool]


class Cancelled(Exception):
    """The caller asked to stop. Raised between steps, so nothing is left half-done."""


class JobResult(NamedTuple):
    """One analyzed job: what failed, where, and what to tell the reader.

    Attributes:
        job: The failed job, log and sections attached.
        attribution: The classifier's verdict. Owns the phase; the LLM may not.
        report: The final report, validated and redacted.
        bundle: The evidence the report was built from: the blamed lines, the
            matchers that fired, what was truncated. For a caller doing its own
            explaining, which needs what the LLM would have seen, not just the
            report. Not redacted: the caller redacts what it emits (invariant #5).
    """

    job: Job
    attribution: Attribution
    report: Report
    bundle: EvidenceBundle | None = None


def process_job(
    job: Job,
    cfg: Config,
    plan: Plan,
    segmenter: LogSegmenter | None = None,
    client: LLMClient | None = None,
) -> JobResult:
    """Segment, classify, assemble evidence and produce the report for one job.

    Deterministic when the LLM is disabled or unconfigured; one LLM call otherwise.

    Args:
        job: The failed job, log already attached.
        cfg: The effective config.
        plan: The run's compiled matchers.
        segmenter: Parser for this log's format. Defaults to the one `config.ci`
            names, which is right for a live run — the provider that produced the
            log is the one we just read it from.
        client: The run's shared LLM client, from `llm.report.client_for_run`.
            None lets `produce_report` build its own, which is what a single-job
            caller wants.

    Returns:
        The job, its verdict, and its report.
    """
    from ci_doctor.core.analyze import build_bundle
    from ci_doctor.core.attribution import attribute
    from ci_doctor.core.phases import assign_phases
    from ci_doctor.llm.report import produce_report

    job.sections = (segmenter or make_segmenter(cfg)).segment(job.log or "")
    log.debug(f"job {job.name}: {len(job.sections)} top-level sections")
    assign_phases(job.sections, cfg.phases)
    attr = attribute(job, job.sections)
    log.debug(
        f"job {job.name}: attribution phase={attr.phase} reason={attr.reason} rule={attr.rule_id} confidence={attr.confidence}",
    )
    bundle = build_bundle(job, attr, job.sections, cfg, plan)
    report = produce_report(job, attr, bundle, cfg, client=client)
    log.debug(
        f"job {job.name}: report category={report.category} confidence={report.confidence} infra={report.is_infra_not_code}",
    )
    return JobResult(job, attr, report, bundle)


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


def analyze_run(
    run_id: str,
    cfg: Config,
    matchers: list[Spec],
    job_id: str | None = None,
    *,
    provider: CIProvider | None = None,
    on_progress: Progress | None = None,
    should_cancel: CancelCheck | None = None,
) -> tuple[Run, CIProvider, list[JobResult]]:
    """Fetch a run from the provider and analyze its failed jobs.

    Args:
        run_id: The provider's run/pipeline id.
        cfg: The effective config.
        matchers: The evidence matchers, loaded from the configured registries.
        job_id: Restrict to a single job id, or None for all failed ones.
        provider: An adapter already connected to the right repository (as
            `service.resolve` returns one), or None to build one from `cfg`.
        on_progress: Told as each log is fetched and each job analyzed.
        should_cancel: Asked between steps; True stops with :class:`Cancelled`.

    Returns:
        A ``(run, provider, results)`` tuple. The provider is returned so the
        caller can post an MR note without reconnecting.

    Raises:
        Cancelled: When `should_cancel` said so.
    """
    from ci_doctor.core.select import select_failed_jobs

    provider = provider or make_ci_provider(cfg)
    _check(should_cancel)
    run = provider.fetch_run(run_id)
    jobs = select_failed_jobs(run.jobs, cfg.analysis.include_allowed_failures)
    if job_id is not None:
        jobs = [j for j in jobs if j.id == job_id]
    log.debug(f"run {run_id}: {len(run.jobs)} jobs, {len(jobs)} failed and selected")

    selected = _ordered(jobs)[: cfg.analysis.max_jobs_analyzed]
    log.info(f"analyzing {len(selected)} failed job(s) from run {run_id}")
    # Sequential on purpose: python-gitlab and PyGithub make no thread-safety promise.
    for done, job in enumerate(selected, 1):
        _check(should_cancel)
        job.log = provider.fetch_job_log(job)
        if on_progress is not None:
            on_progress("log", done, len(selected))
    return (
        run,
        provider,
        _analyze(selected, cfg, matchers, on_progress=on_progress, should_cancel=should_cancel),
    )


def _check(should_cancel: CancelCheck | None) -> None:
    """Stop here if the caller asked to.

    Args:
        should_cancel: The caller's check, or None.

    Raises:
        Cancelled: When it says so.
    """
    if should_cancel is not None and should_cancel():
        raise Cancelled


def _analyze(
    jobs: list[Job],
    cfg: Config,
    matchers: list[Spec],
    segmenter: LogSegmenter | None = None,
    *,
    on_progress: Progress | None = None,
    should_cancel: CancelCheck | None = None,
) -> list[JobResult]:
    """Analyze every job, concurrently when the config allows it.

    The jobs are independent and the LLM call dominates each one, so this is where
    a run's wall-clock time actually goes. Order is preserved regardless of which
    job finishes first — the reader expects pipeline order, not completion order.

    Args:
        jobs: The selected jobs, logs already attached.
        cfg: The effective config; `analysis.max_parallel_jobs` sets the width.
        matchers: The evidence matchers, loaded from the configured registries.
        segmenter: Parser for the log format, or None to derive it per job.
        on_progress: Told as each job is analyzed.
        should_cancel: Asked before each job; True stops with :class:`Cancelled`.

    Returns:
        One result per job, in the order given.

    Raises:
        Cancelled: When `should_cancel` said so. Jobs already running finish first.
    """
    from ci_doctor.llm.report import client_for_run

    plan = compile_plan(matchers, cfg.matchers.exclude)
    client = client_for_run(cfg)
    done, lock = 0, threading.Lock()

    def one(job: Job) -> JobResult:
        nonlocal done
        _check(should_cancel)
        result = process_job(job, cfg, plan, segmenter, client)
        if on_progress is not None:
            with lock:  # workers finish in any order; the count still goes up by one
                done += 1
                on_progress("job", done, len(jobs))
        return result

    try:
        workers = min(cfg.analysis.max_parallel_jobs, len(jobs))
        if workers <= 1:
            return [one(job) for job in jobs]
        log.debug(f"analyzing {len(jobs)} job(s) across {workers} workers")
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ci-doctor") as pool:
            # `map` yields in submission order, so the report reads chronologically even
            # though the jobs finished in whatever order the endpoint answered them.
            return list(pool.map(one, jobs))
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
