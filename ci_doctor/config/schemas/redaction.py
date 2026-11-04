"""`redaction:`: secret scrubbing."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict
from ci_doctor.config.schemas._regex import Re2


class RedactionConfig(Strict):
    """Secret scrubbing. Applied before anything is rendered, written or sent."""

    enabled: bool = Field(
        default=True, description="Scrub tokens, credentials in URLs and env secrets from all output."
    )
    extra_patterns: list[Re2] = Field(
        default_factory=list, description="Additional regexes (RE2) to scrub, on top of the built-in set."
    )
