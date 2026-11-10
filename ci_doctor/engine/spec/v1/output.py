"""An output (one meaning, one description) and its variants (one spelling each)."""

from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from ci_doctor.engine.spec.base import Strict
from ci_doctor.engine.spec.limits import MAX_VARIANTS
from ci_doctor.engine.spec.roles import Role
from ci_doctor.engine.spec.text import Description
from ci_doctor.engine.spec.v1.match import Node
from ci_doctor.engine.spec.v1.window import Window

#: An output or variant key: what follows the dot in `ruff.check`.
Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class Variant(Strict):
    """One spelling of an output. Shares its output's description; needs its own positive fixture."""

    match: Node = Field(description="Finds this spelling.")
    window: Window | None = Field(None, description="Only when this spelling needs a different window.")


class Output(Strict):
    """One distinct output of a tool: one meaning, one description."""

    description: Description = Field(
        description="What the evidence means; prompt text. 20-250 printable ASCII."
    )
    role: Role = Field(
        "tool", description="Ranks the window under budget pressure and tells the model how to read it."
    )
    match: Node | None = Field(None, description="Finds the anchor lines. Set this or `variants`.")
    variants: dict[Key, Variant] | None = Field(
        None, min_length=1, max_length=MAX_VARIANTS, description="One spelling each. Set this or `match`."
    )
    window: Window | None = Field(None, description="Which lines around the anchor become evidence.")

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        """Require exactly one of `match` and `variants`.

        Returns:
            The output, unchanged.

        Raises:
            ValueError: On neither or both.
        """
        if (self.match is None) == (self.variants is None):
            raise ValueError("set exactly one of `match` or `variants`")
        return self
