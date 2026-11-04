"""`denoise:`: log cleanup applied before evidence extraction."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict
from ci_doctor.config.schemas._regex import Re2


class DenoiseConfig(Strict):
    """Log cleanup applied before evidence extraction."""

    collapse_carriage_returns: bool = Field(
        default=True, description=r"Collapse \r progress bars to their final rendered state."
    )
    dedupe_repeats: bool = Field(True, description='Fold repeated lines into "<line>  (×47)".')
    noise_patterns: list[Re2] = Field(
        default_factory=list, description="Regexes (RE2) whose matching lines are dropped outright."
    )
