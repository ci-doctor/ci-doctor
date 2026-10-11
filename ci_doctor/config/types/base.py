"""Base for every config model."""

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    """Base for every config section: unknown keys raise instead of being ignored."""

    model_config = ConfigDict(extra="forbid")
