"""The brief: a failed job's evidence, handed to an agent to explain instead of to a model.

`analyze` asks an LLM of its own for the explanation. A coding agent calling
ci-doctor already is one, so it gets what that LLM would have been shown — the
settled verdict, the budgeted excerpt, the matchers behind it and the analyst's
instructions — and writes the explanation itself. No model is called here.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import Confidence, FailureReason, Phase
from ci_doctor.core.redact import redact_text
from ci_doctor.llm.report import instructions
from ci_doctor.matchers.registry import load as load_matchers
from ci_doctor.service.analyze import JobResult, analyze_log, analyze_run


class FiredMatcher(BaseModel):
    """A matcher output whose anchor reached the excerpt."""

    id: str = Field(description="`registry/matcher`, or `registry/matcher.output`.")
    role: str = Field(description="issue, tool, gate, wrapper or fallback: how to read its lines.")
    description: str = Field(description="What its evidence means, in the matcher author's words.")


class Brief(BaseModel):
    """Everything known about one failed job, for the reader to explain."""

    job_id: str = Field(description="The CI system's job id.")
    job_name: str = Field(description="The job's name.")
    stage: str | None = Field(description="The job's stage, when the CI system has stages.")
    web_url: str | None = Field(description="Where the job can be opened, when known.")
    failure_phase: Phase = Field(description="Where the job failed. Decided deterministically; settled.")
    reason: FailureReason = Field(description="The normalised failure reason behind that phase.")
    rule: str = Field(description="Which attribution rule decided the phase.")
    confidence: Confidence = Field(description="How much to trust the phase.")
    terminal_evidence: str | None = Field(description="The single most telling line, when one exists.")
    secondary_phases: list[Phase] = Field(description="Phases that only warned: context, not the cause.")
    excerpt: str = Field(description="Log lines from the failed phase, verbatim, secrets redacted.")
    truncated: bool = Field(description="Whether the token budget dropped lines from the excerpt.")
    matchers: list[FiredMatcher] = Field(description="The matchers that selected the excerpt, in log order.")
    instructions: str = Field(description="How to read this brief and what to explain.")


def brief(result: JobResult, cfg: Config) -> Brief:
    """Turn one analyzed job into its brief, redacted (invariant #5).

    Args:
        result: The analyzed job.
        cfg: The effective config, for its redaction rules.

    Returns:
        The brief.
    """
    job, attr, _, bundle = result
    terminal = attr.terminal_evidence
    return Brief(
        job_id=job.id,
        job_name=job.name,
        stage=job.stage,
        web_url=job.web_url,
        failure_phase=attr.phase,
        reason=attr.reason,
        rule=attr.rule_id,
        confidence=attr.confidence,
        terminal_evidence=redact_text(terminal, cfg.redaction) if terminal else None,
        secondary_phases=attr.secondary_phases,
        excerpt=redact_text("\n".join(bundle.blamed_lines), cfg.redaction),
        truncated=bundle.truncated,
        matchers=[
            FiredMatcher(id=m.id, role=m.role, description=m.description or "") for m in bundle.matchers
        ],
        instructions=instructions(attr),
    )


def _without_llm(cfg: Config) -> Config:
    """The config with the LLM step off: the reader of a brief is the model.

    Args:
        cfg: The effective config.

    Returns:
        A copy whose analysis makes no model call, whatever `llm:` says.
    """
    return cfg.model_copy(update={"llm": cfg.llm.model_copy(update={"enabled": False})})


def run_briefs(run_id: str, cfg: Config, job_id: str | None = None) -> list[Brief]:
    """Brief every failed job of a live run.

    Args:
        run_id: The provider's run/pipeline id.
        cfg: The effective config.
        job_id: Restrict to a single job id, or None for all failed ones.

    Returns:
        One brief per failed job, in pipeline order; empty when none failed.

    Raises:
        SetupError: If the selected matchers cannot be loaded.
    """
    _, _, results = analyze_run(run_id, _without_llm(cfg), load_matchers(cfg.matchers), job_id)
    return [brief(r, cfg) for r in results]


def log_briefs(path: Path, cfg: Config) -> list[Brief]:
    """Brief a captured job log, offline.

    Args:
        path: The log file.
        cfg: The effective config.

    Returns:
        One brief per job in the log: today always exactly one.

    Raises:
        SetupError: If the selected matchers cannot be loaded.
        OSError: If the file cannot be read.
    """
    return [brief(r, cfg) for r in analyze_log(path, _without_llm(cfg), load_matchers(cfg.matchers))]
