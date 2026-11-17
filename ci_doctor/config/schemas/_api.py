"""Limits on a provider's REST API calls, shared by `github:` and `gitlab:`."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict


class ApiLimits(Strict):
    """How far ci-doctor goes with one API before it gives up instead of hanging."""

    max_pages: int = Field(
        default=100,
        ge=1,
        le=10_000,
        description="Most pages of one list to read (100 items each). Past it the call fails: a cut list "
        "would hide jobs.",
    )
    rate_limit_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="How many times to wait out a rate limit and retry a call. 0 fails on the first one.",
    )
    rate_limit_max_wait_seconds: float = Field(
        default=60.0,
        ge=0,
        le=3600,
        description="Longest single rate-limit wait. A longer one fails at once, so a CI job never sits "
        "idle until its own timeout.",
    )
