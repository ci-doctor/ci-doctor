"""Base for every matcher spec model."""

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    """Unknown keys raise instead of being ignored: a typo in a matcher file is an error.

    The same rule as the config's own base; the engine imports nothing from `config/`.
    """

    model_config = ConfigDict(extra="forbid")
