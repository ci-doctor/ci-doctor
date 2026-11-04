"""`extraction:`: how the causal lines are pulled out of a job log."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict


class ExtractionConfig(Strict):
    """How the causal lines are pulled out of a job log."""

    tail_lines: int = Field(
        default=120, description="Lines kept from the end of the blamed section as a fallback."
    )
