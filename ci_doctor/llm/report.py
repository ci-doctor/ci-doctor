"""Produce the Report: one LLM call plus one repair retry, or no call at all.

A deterministic report stands in whenever the LLM is disabled, unconfigured or
unreachable.

The deterministic report is a first-class output (`llm.enabled: false`) *and*
the degraded fallback — it always carries phase, reason, terminal evidence, an
excerpt, and templated remediation. Nothing here crashes; a broken analyzer must
never change the pipeline outcome.
"""

import json
import logging
import time
from collections.abc import Mapping
from importlib import resources
from string import Template
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ci_doctor.config.schemas import Config
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.evidence import EvidenceBundle
from ci_doctor.core.models import FailureReason, Job, Phase
from ci_doctor.core.ports import LLMClient
from ci_doctor.core.redact import redact_report
from ci_doctor.lib.files import ENCODING
from ci_doctor.llm.schema import Category, Evidence, RemediationStep, Report

if TYPE_CHECKING:
    from ci_doctor.service.brief import Brief, FiredMatcher

log = logging.getLogger("ci_doctor.llm")

#: Templated fix advice per failure reason, used by the deterministic report.
_REMEDIATION = {
    FailureReason.NO_RUNNER: "Check that a runner with the required tags is online and not saturated.",
    FailureReason.RUNNER_SYSTEM: "Retry the job; if it recurs, check runner/executor health and image availability.",
    FailureReason.TIMEOUT: "Increase the job timeout or optimize the slow step; look for a hang.",
    FailureReason.MISSING_DEPENDENCY: "Ensure the upstream job that produces the artifact succeeded and needs/dependencies are correct.",
    FailureReason.UNMET_PREREQUISITES: "Verify job prerequisites (environments, approvals, variables) are satisfied.",
    FailureReason.SCRIPT_FAILURE: "Inspect the terminal command output in the excerpt and fix the failing command.",
}
#: Categories the provider reason settles on its own. A reason not here (notably
#: `script_failure`) leaves the category to the model — the deterministic report
#: does not guess an ecosystem from the log, which is the model's job.
_CATEGORY: dict[FailureReason, Category] = {
    FailureReason.NO_RUNNER: "infrastructure",
    FailureReason.RUNNER_SYSTEM: "infrastructure",
    FailureReason.TIMEOUT: "timeout",
    FailureReason.MISSING_DEPENDENCY: "dependency",
}


def _infer_category(reason: FailureReason) -> Category:
    """Categorise from the provider reason alone; "unknown" when it can't decide.

    Deliberately does not read the log: classifying a script failure as test/build/
    runtime means matching tool-specific regexes (pytest, tsc, go…) that duplicate the
    matcher catalogue and belong to the model. Without an LLM the category is honestly
    "unknown" — the phase still says where it broke.
    """
    return _CATEGORY.get(reason, "unknown")


def client_for_run(cfg: Config, environ: Mapping[str, str] | None = None) -> LLMClient | None:
    """Build the single LLM client a whole run shares.

    Built once per run rather than once per job: every backend holds a connection
    pool (and the `openai` one remembers whether the server accepts
    `response_format`), so a client per job threw both away 10 times over.

    Degrades to None rather than raising, exactly like `produce_report` — a broken
    backend must still leave the deterministic report standing (invariant #3).

    Args:
        cfg: The effective config.
        environ: Environment for API keys. Defaults to os.environ.

    Returns:
        The client, or None when the LLM is disabled, unconfigured, or failed to
        build. `produce_report` then takes its own deterministic path.
    """
    from ci_doctor.llm.backends import backend_ready, make_client

    if not (cfg.llm.enabled and backend_ready(cfg.llm)):
        log.debug(f"LLM disabled/not ready (backend={cfg.llm.backend}); no client for this run")
        return None
    try:
        return make_client(cfg.llm, environ=environ)
    except Exception as exc:  # noqa: BLE001 - never crash on a bad backend
        log.info(f"LLM backend {cfg.llm.backend} unavailable ({exc}); deterministic reports")
        return None


def produce_report(
    job: Job,
    attr: Attribution,
    bundle: EvidenceBundle,
    cfg: Config,
    brief: "Brief",
    *,
    client: LLMClient | None = None,
    environ: Mapping[str, str] | None = None,
) -> Report:
    """Produce the final report, with or without an LLM.

    Degrades at every step rather than raising: LLM disabled, backend not
    configured, backend unavailable, and an invalid reply after one repair retry
    all end in the deterministic report. The output is always redacted.

    Args:
        job: The failed job.
        attr: The classifier's verdict. The LLM may not overturn its phase.
        bundle: The budgeted evidence.
        cfg: Config supplying the LLM and redaction settings.
        brief: The job's brief, already redacted; the prompt is built from it alone.
        client: Pre-built LLM client, used by tests to avoid a real backend.
        environ: Environment for redaction and API keys. Defaults to os.environ.

    Returns:
        A validated, redacted report.
    """
    from ci_doctor.llm.backends import backend_ready, make_client

    if str(attr.reason) in cfg.analysis.skip_llm_for:
        # The reason already settles it (no runner, missing artifact, cancelled):
        # a model has nothing left to explain, so don't pay for the call.
        log.info(f"job {job.name}: deterministic report (reason {attr.reason} is in analysis.skip_llm_for)")
        return redact_report(deterministic_report(job, attr, bundle), cfg.redaction, environ)

    if not (cfg.llm.enabled and (client is not None or backend_ready(cfg.llm))):
        log.info(f"job {job.name}: deterministic report (no LLM configured)")
        return redact_report(deterministic_report(job, attr, bundle), cfg.redaction, environ)

    if client is None:
        try:
            client = make_client(cfg.llm, environ=environ)
        except Exception as exc:  # noqa: BLE001 - never crash on a bad backend
            log.info(f"LLM backend {cfg.llm.backend} unavailable ({exc}); deterministic report")
            return redact_report(deterministic_report(job, attr, bundle), cfg.redaction, environ)

    # The brief is redacted where it is built, so the prompt is too (invariant #5).
    prompt = _prompt(brief)
    log.info(
        f"job {job.name}: calling LLM backend={cfg.llm.backend} model={cfg.llm.model} (may take a while)"
    )
    log.debug(f"prompt={len(prompt)} chars")
    start = time.perf_counter()
    report = _call_and_validate(client, prompt)
    log.info(f"job {job.name}: LLM answered in {time.perf_counter() - start:.1f}s")
    if report is None:
        log.info("no valid LLM report; using deterministic fallback")
        report = deterministic_report(job, attr, bundle, degraded=True)
    else:
        log.debug("LLM returned a valid report")
        if report.failure_phase != attr.phase:
            # Invariant #1, enforced here and not only in the prompt: attribution
            # owns the phase. A model that argues with it gets overruled, silently
            # for the reader but loudly in the log.
            log.warning(
                f"LLM answered phase={report.failure_phase}; overruled by attribution phase={attr.phase} (rule {attr.rule_id})",
            )
            report = report.model_copy(update={"failure_phase": attr.phase})
    return redact_report(report, cfg.redaction, environ)


def _call_and_validate(client: LLMClient, prompt: str) -> Report | None:
    """Call the LLM and validate its reply.

    No hand-rolled retry here: every backend already retries once internally
    on a schema-invalid reply (`Agent(retries=1)`); a second one would just
    duplicate it (see `149313e`).

    Args:
        client: The LLM client.
        prompt: The rendered prompt.

    Returns:
        The validated report, or None on any failure — the caller degrades to
        the deterministic report either way.
    """
    try:
        return client.complete_structured(prompt, Report)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        log.info(f"LLM reply invalid; using deterministic fallback: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - network/LLM failure -> deterministic fallback
        log.warning(f"LLM call failed, using deterministic fallback: {exc}")
        return None


def _capped(text: str, limit: int = 2000) -> str:
    """Cap the excerpt for the report field, announcing the cut.

    Keeps the head and the tail, matching `budget.fit`: the head holds the first
    error, the tail how the run ended. The marker is the point — a silent cut is a
    lie about the evidence, and this is the last place the evidence can be lost
    before a human reads it.

    Args:
        text: The excerpt.
        limit: Maximum characters to keep.

    Returns:
        The text, or its first third and last two thirds of `limit` around an
        elision marker.
    """
    if len(text) <= limit:
        return text
    head = limit // 3
    return f"{text[:head]}\n… [{len(text) - limit} characters elided] …\n{text[-(limit - head) :]}"


def deterministic_report(
    job: Job, attr: Attribution, bundle: EvidenceBundle, *, degraded: bool = False
) -> Report:
    """Build a full report from the deterministic pipeline alone.

    Not a stub: this is a first-class output (`llm.enabled: false`) *and* the
    degraded fallback. It always carries a phase, reason, terminal evidence, an
    excerpt and templated remediation.

    Args:
        job: The failed job.
        attr: The classifier's verdict.
        bundle: The budgeted evidence.
        degraded: Whether this stands in for a failed LLM call. When true, that
            fact is added to the contributing factors so the reader is never
            misled about which path produced the report.

    Returns:
        A valid report. Not yet redacted — callers do that.
    """
    # No second cut: `blamed_lines` is already denoised, windowed and budget-fitted.
    # Re-trimming discards that selection — see GUIDELINES §7.
    blamed = "\n".join(bundle.blamed_lines)
    excerpt = blamed if bundle.blamed_lines else (attr.terminal_evidence or "")
    is_infra = attr.phase in (Phase.PROVISION, Phase.PREPARE) or attr.reason in (
        FailureReason.RUNNER_SYSTEM,
        FailureReason.NO_RUNNER,
    )
    remediation = _REMEDIATION.get(attr.reason, "Inspect the log excerpt and address the failing step.")
    if attr.rule_id == "oom_137":
        remediation = "Reduce memory use or raise the runner memory limit (exit 137 = OOM/SIGKILL)."

    factors = [f"warnings in the {p} phase (non-causal)" for p in attr.secondary_phases]
    if job.log_error:
        factors.append(
            f"the job's log could not be read ({job.log_error}), so there is no log excerpt and the "
            "phase rests on the CI system's own failure reason alone."
        )
    if degraded:
        factors.append("ci-doctor could not reach the LLM; this is the deterministic fallback report.")

    category = _infer_category(attr.reason)
    return Report(
        summary=f"{job.name} failed in the {attr.phase} phase ({attr.reason})."[:140],
        failure_phase=attr.phase,
        category=category,
        confidence=attr.confidence,
        is_infra_not_code=is_infra,
        likely_flaky=False,
        root_cause=attr.terminal_evidence
        or f"Deterministic analysis attributes the failure to the {attr.phase} phase (rule '{attr.rule_id}').",
        contributing_factors=factors,
        evidence=[
            Evidence(
                section=str(attr.phase),
                excerpt=_capped(excerpt),
                why_it_matters="Terminal evidence selected by deterministic attribution.",
            )
        ],
        remediation=[
            RemediationStep(
                order=1, action=remediation, rationale=f"reason={attr.reason}, rule={attr.rule_id}"
            )
        ],
        related_paths=[],
        handoff_prompt=_handoff(job, attr, excerpt),
    )


def _handoff(job: Job, attr: Attribution, excerpt: str) -> str:
    """Compose the prompt a developer can paste into their own coding agent.

    Args:
        job: The failed job.
        attr: The classifier's verdict.
        excerpt: The evidence lines.

    Returns:
        Plain prose with no markup, so copying it out of the terminal yields
        exactly what an agent should receive.
    """
    return (
        f'CI job "{job.name}" failed in the {attr.phase} phase '
        f"(reason: {attr.reason}, rule: {attr.rule_id}).\n"
        f"Terminal evidence:\n{attr.terminal_evidence or 'n/a'}\n\n"
        f"Relevant log excerpt:\n{excerpt}\n\n"
        "Diagnose the root cause and implement a fix. Do not change unrelated code."
    )


def _prompt(brief: "Brief") -> str:
    """Build the prompt for the configured LLM from a brief: its instructions, then its data.

    Reads nothing but the brief, so the model sees what an agent holding the brief
    sees. Templates live in `llm/prompts/` (invariant #8).

    Args:
        brief: The job's brief, already redacted.

    Returns:
        The combined prompt.
    """
    user = Template(_load("analyze.user.txt")).safe_substitute(
        job=brief.job_name,
        stage=brief.stage or "?",
        failure_reason=str(brief.reason),
        raw_failure_reason=brief.raw_reason,
        duration=brief.duration,
        needs=", ".join(brief.needs) or "none",
        run_jobs="\n".join(brief.run_jobs) or "n/a",
        terminal=brief.terminal_evidence or "n/a",
        log_error=brief.log_error or "none",
        phase=brief.failure_phase,
        secondary=", ".join(str(p) for p in brief.secondary_phases) or "none",
        excerpt=brief.excerpt,
        packs=_packs(brief.matchers),
    )
    return brief.instructions + "\n\n---\n\n" + user


def _packs(matchers: "list[FiredMatcher]") -> str:
    """One JSON object per matcher behind the excerpt: its id, role and author's description.

    A description comes from a registry, i.e. from someone else. JSON-quoting it
    keeps it inside its own string, so it can't close the prompt's matcher section
    or open a new one, whatever quotes, dashes or markers it carries.

    Args:
        matchers: The brief's matchers.

    Returns:
        The lines for the prompt's matcher section, or a note that no matcher fired.
    """
    lines = [json.dumps(m.model_dump(), ensure_ascii=False) for m in matchers]
    return "\n".join(lines) or "none — no pack matched; the excerpt is the log's tail, or all of it"


def _load(name: str) -> str:
    """Read a packaged prompt template.

    Args:
        name: File name under `llm/prompts/`, e.g. "analyze.user.txt".

    Returns:
        The template text.
    """
    return resources.files("ci_doctor.llm.prompts").joinpath(name).read_text(encoding=ENCODING)
