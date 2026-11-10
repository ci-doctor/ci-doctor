"""A `schema: 1` matcher file: one tool, wrapper or issue, with one output or several."""

from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from ci_doctor.engine.spec.base import Strict
from ci_doctor.engine.spec.limits import MAX_DEFINES, MAX_OUTPUTS, MAX_TAGS
from ci_doctor.engine.spec.roles import Role
from ci_doctor.engine.spec.text import Description, Tag
from ci_doctor.engine.spec.v1.match import Node
from ci_doctor.engine.spec.v1.output import Key, Output, Variant
from ci_doctor.engine.spec.v1.window import Window

#: A `define` name: `$NAME` in a template, `%{NAME}` in a regex.
DefineName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")]


class Matcher(Strict):
    """A matcher in schema 1.

    One output: `role`, `match` (or `variants`) and `window` sit at the top, and
    `description` is the prompt text. Several: they sit under `outputs`, and
    `description` is a one-line summary for people.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(
        alias="schema", description="The format version this file is written in."
    )
    id: str = Field(
        pattern=r"^[A-Za-z0-9_./-]+$",
        description="Matcher id: bare in a registry file (`ruff`), `alias/id` once loaded. Equals its folder name.",
    )
    description: Description = Field(
        description="With one output, the prompt text. With `outputs`, a one-line summary for people."
    )
    tags: list[Tag] = Field(
        min_length=1, max_length=MAX_TAGS, description=f"1-{MAX_TAGS} tags, for search and filters."
    )
    define: dict[DefineName, Annotated[str, StringConstraints(min_length=1)]] = Field(
        default_factory=dict,
        max_length=MAX_DEFINES,
        description="Named pattern pieces: `$NAME` in a template, `%{NAME}` in a regex.",
    )
    outputs: dict[Key, Output] | None = Field(
        None, min_length=1, max_length=MAX_OUTPUTS, description="The outputs, when there are several."
    )
    role: Role | None = Field(None, description="One output only. Default `tool`.")
    match: Node | None = Field(None, description="One output only: finds the anchor lines.")
    variants: dict[Key, Variant] | None = Field(None, description="One output only: one spelling each.")
    window: Window | None = Field(None, description="One output only: lines kept around the anchor.")

    @model_validator(mode="after")
    def _one_shape(self) -> Self:
        """Require `outputs`, or a top-level `match` / `variants`, never both.

        Returns:
            The matcher, unchanged.

        Raises:
            ValueError: On neither, both, or one-output fields beside `outputs`.
        """
        single = [k for k in ("role", "match", "variants", "window") if getattr(self, k) is not None]
        if self.outputs is not None and single:
            raise ValueError(f"with `outputs`, {', '.join(f'`{k}`' for k in single)} belong on an output")
        if self.outputs is None and (self.match is None) == (self.variants is None):
            raise ValueError("set `outputs`, or exactly one of `match` or `variants`")
        return self

    def named_outputs(self) -> list[tuple[str, str, Output]]:
        """Every output with its id and its place in the file.

        Returns:
            `(id, path, output)`: `ruff.check` and `outputs.check` for one of several;
            the matcher's own id and "" for a one-output matcher.
        """
        if self.outputs is not None:
            return [(f"{self.id}.{k}", f"outputs.{k}", o) for k, o in self.outputs.items()]
        own = Output.model_construct(
            description=self.description,
            role=self.role or "tool",
            match=self.match,
            variants=self.variants,
            window=self.window,
        )
        return [(self.id, "", own)]
