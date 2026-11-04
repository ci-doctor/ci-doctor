"""Pydantic config model, and the source of truth for the published JSON Schema.

``extra="forbid"`` everywhere means an unknown key is an error, not a silent typo.
Scalar defaults live here; the data-heavy baseline (phase map, the official matcher
registry, noise patterns) ships in ``defaults.yml``.

Every field carries a ``description``: it is the text editors show when completing a
``.ci-doctor.yml`` against the schema emitted by ``ci-doctor config --schema``, so a
field added without one ships an undocumented knob.

One module per top-level key of `.ci-doctor.yml` (`llm.py` is `llm:`), the way `cli/`
mirrors the commands; this package is the document itself, `Config`. Shared field
types live in the underscored modules.
"""

import logging

from pydantic import ConfigDict, Field, model_validator

from ci_doctor.config.schemas._base import Strict
from ci_doctor.config.schemas.analysis import AnalysisConfig
from ci_doctor.config.schemas.denoise import DenoiseConfig
from ci_doctor.config.schemas.extraction import ExtractionConfig
from ci_doctor.config.schemas.github import GitHubConfig
from ci_doctor.config.schemas.gitlab import GitLabConfig
from ci_doctor.config.schemas.llm import LLMConfig
from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.config.schemas.output import OutputConfig
from ci_doctor.config.schemas.redaction import RedactionConfig

log = logging.getLogger("ci_doctor.config")

#: Canonical URL of the published schema. A `.ci-doctor.yml` points its
#: `# yaml-language-server: $schema=` comment here to get editor completion.
SCHEMA_ID = "https://github.com/ci-doctor/ci-doctor/releases/latest/download/ci-doctor.schema.json"


class Config(Strict):
    """The full ``.ci-doctor.yml`` document. Every key is optional."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"$id": SCHEMA_ID},
    )

    ci: str = Field(
        default="github",
        description=(
            "Which CI system to read the failed run from: github or gitlab. Only used for a live "
            "run — replaying a log file detects the format from the log itself."
        ),
    )
    scm: str | None = Field(
        default=None,
        description=(
            "Which git host to post the MR/PR note to. Defaults to `ci`, which is right whenever the "
            "CI system is also the git host. Set it for mixed setups — `ci: jenkins` with `scm: gitlab`. "
            'Use "none" to never post.'
        ),
    )
    provider: str | None = Field(
        default=None,
        description="Deprecated alias for `ci`, kept for one release. Set `ci` instead.",
        deprecated=True,
    )
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig, description="GitLab connection settings.")
    github: GitHubConfig = Field(default_factory=GitHubConfig, description="GitHub connection settings.")
    llm: LLMConfig = Field(default_factory=LLMConfig, description="LLM explanation step.")
    analysis: AnalysisConfig = Field(
        default_factory=AnalysisConfig, description="Job selection and LLM skipping."
    )
    phases: dict[str, str] = Field(
        default_factory=dict,
        description="Section/step name -> phase (setup, script, after_script, ...). Deep-merged onto the shipped map.",
    )
    matchers: MatchersConfig = Field(
        default_factory=MatchersConfig, description="Matcher registries, and what to load from them."
    )
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig, description="Evidence extraction.")
    denoise: DenoiseConfig = Field(default_factory=DenoiseConfig, description="Log cleanup.")
    output: OutputConfig = Field(default_factory=OutputConfig, description="Report destinations.")
    redaction: RedactionConfig = Field(default_factory=RedactionConfig, description="Secret scrubbing.")

    @model_validator(mode="after")
    def _fold_deprecated_provider(self) -> "Config":
        """Accept the old `provider` key as `ci`, and refuse a contradiction.

        Returns:
            Self, with `ci` carrying the old key's value when only it was set.

        Raises:
            ValueError: If both keys are set to different systems. Guessing which
                one the user meant would silently read the wrong CI.
        """
        # Read through __dict__: `deprecated=True` turns plain attribute access into
        # a DeprecationWarning, and this validator runs on every single config load.
        provider = self.__dict__.get("provider")
        if provider is None:
            return self
        if "ci" in self.model_fields_set and self.ci != provider:
            raise ValueError(f"config sets both ci={self.ci!r} and the deprecated provider={provider!r}")
        log.warning("`provider:` is deprecated and will be removed; rename it to `ci:`")
        self.ci = provider
        return self

    @property
    def scm_vendor(self) -> str:
        """The git host to post the note to.

        Returns:
            The configured `scm`, or `ci` when unset — a CI system that is also a
            git host is its own note target, which covers GitLab and GitHub.
        """
        return self.scm or self.ci
