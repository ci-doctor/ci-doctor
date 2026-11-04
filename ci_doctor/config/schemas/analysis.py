"""`analysis:`: which jobs get analyzed, and which skip the LLM."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict


class AnalysisConfig(Strict):
    """Which jobs get analyzed, and which skip the LLM."""

    include_allowed_failures: bool = Field(
        default=False, description="Analyze jobs marked allow_failure. They are noise by default."
    )
    max_jobs_analyzed: int = Field(default=10, description="Cap on failed jobs analyzed per run.")
    max_parallel_jobs: int = Field(
        default=4,
        ge=1,
        description=(
            "Jobs analyzed concurrently. The LLM call dominates a run and the calls are "
            "independent, so this is the main latency lever. Set 1 for a strictly "
            "sequential run against a rate-limited endpoint."
        ),
    )
    skip_llm_for: list[str] = Field(
        default_factory=lambda: ["no_runner", "missing_dependency", "cancelled"],
        description="Failure reasons already fully determined, so they get a templated report and no LLM call.",
    )
