"""The brief: everything a model is shown about one failed job, built and redacted once.

`analyze` renders it into the prompt for the configured LLM; the MCP server hands
it to the agent as it is. Both read the same data because there is no other path
to a model (GUIDELINES §2.4).
"""

import json
from collections.abc import Mapping
from importlib import resources
from string import Template

from pydantic import BaseModel, Field

from ci_doctor.config.schemas import Config
from ci_doctor.core.analyze import EvidenceBundle
from ci_doctor.core.attribution import Attribution
from ci_doctor.core.models import Confidence, FailureReason, Job, Phase
from ci_doctor.core.redact import redact_text


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
    duration: float | None = Field(description="How long the job ran, in seconds, when known.")
    needs: list[str] = Field(description="The upstream jobs this one depends on.")
    failure_phase: Phase = Field(description="Where the job failed. Decided deterministically; settled.")
    reason: FailureReason = Field(description="The normalised failure reason behind that phase.")
    raw_reason: str = Field(description="The CI system's own failure reason, as it spelled it.")
    rule: str = Field(description="Which attribution rule decided the phase.")
    confidence: Confidence = Field(description="How much to trust the phase.")
    terminal_evidence: str | None = Field(description="The single most telling line, when one exists.")
    log_error: str | None = Field(
        description="Why the job's log could not be read, when it could not. Then the excerpt is empty and "
        "the verdict rests on the CI system's failure reason alone: say so rather than guess a cause."
    )
    secondary_phases: list[Phase] = Field(description="Phases that only warned: context, not the cause.")
    excerpt: str = Field(description="Log lines from the failed phase, verbatim, secrets redacted.")
    truncated: bool = Field(description="Whether the token budget dropped lines from the excerpt.")
    matchers: list[FiredMatcher] = Field(description="The matchers that selected the excerpt, in log order.")
    instructions: str = Field(description="How to read this brief and what to explain.")


def build_brief(
    job: Job,
    attr: Attribution,
    bundle: EvidenceBundle,
    cfg: Config,
    environ: Mapping[str, str] | None = None,
) -> Brief:
    """Turn one diagnosed job into its brief, every text field redacted (invariant #5).

    Args:
        job: The failed job.
        attr: The classifier's verdict.
        bundle: The budgeted evidence.
        cfg: The effective config, for its redaction rules.
        environ: Environment to harvest secret literals from. Defaults to os.environ.

    Returns:
        The brief.
    """

    def clean(text: str) -> str:
        """Scrub one field."""
        return redact_text(text, cfg.redaction, environ)

    return Brief(
        job_id=job.id,
        job_name=clean(job.name),
        stage=clean(job.stage) if job.stage else job.stage,
        web_url=job.web_url,
        duration=job.duration,
        needs=[clean(n) for n in job.needs],
        failure_phase=attr.phase,
        reason=attr.reason,
        raw_reason=clean(job.raw_failure_reason),
        rule=attr.rule_id,
        confidence=attr.confidence,
        terminal_evidence=clean(attr.terminal_evidence) if attr.terminal_evidence else None,
        log_error=clean(job.log_error) if job.log_error else None,
        secondary_phases=attr.secondary_phases,
        excerpt=clean("\n".join(bundle.blamed_lines)),
        truncated=bundle.truncated,
        matchers=[
            FiredMatcher(id=m.id, role=m.role, description=clean(m.description or ""))
            for m in bundle.matchers
        ],
        instructions=instructions(attr),
    )


def instructions(attr: Attribution) -> str:
    """The analyst's standing orders for one job: the phase is settled, explain within it.

    Args:
        attr: The classifier's verdict, stated as *settled*.

    Returns:
        The filled system template.
    """
    return Template(_load("analyze.system.txt")).safe_substitute(
        phase=attr.phase,
        rule_id=attr.rule_id,
        confidence=attr.confidence,
    )


def render_prompt(brief: Brief) -> str:
    """The brief as the prompt for an LLM of our own: its instructions, then its data.

    Reads nothing but the brief, so the configured model sees what an agent holding
    the brief sees. Templates live in `llm/prompts/` (invariant #8).

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
        terminal=brief.terminal_evidence or "n/a",
        log_error=brief.log_error or "none",
        phase=brief.failure_phase,
        secondary=", ".join(str(p) for p in brief.secondary_phases) or "none",
        excerpt=brief.excerpt,
        packs=_packs(brief.matchers),
    )
    return brief.instructions + "\n\n---\n\n" + user


def _packs(matchers: list[FiredMatcher]) -> str:
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
        name: File name under `llm/prompts/`, e.g. "analyze.system.txt".

    Returns:
        The template text.
    """
    return resources.files("ci_doctor.llm.prompts").joinpath(name).read_text()
