"""A `window`: which lines around an anchor become evidence."""

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from ci_doctor.engine.spec.base import Strict
from ci_doctor.engine.spec.limits import MAX_REACH
from ci_doctor.engine.spec.v1.match import Near, Node


class Window(Strict):
    """Lines kept around each anchor. The default is the anchor line alone."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    before: int = Field(0, ge=0, le=100, description="Lines kept above the anchor, 0-100.")
    after: int = Field(0, ge=0, le=100, description="Lines kept below the anchor, 0-100.")
    from_: Near | None = Field(
        None, alias="from", description="Start at the nearest matching line above, looking `within` lines up."
    )
    until: Node | None = Field(None, description="End at the next matching line, inclusive.")
    while_: Node | None = Field(
        None, alias="while", description="Keep extending while the next line matches."
    )
    max_lines: int | None = Field(
        None,
        ge=1,
        le=MAX_REACH,
        description=f"Lines from the anchor down, at most; required with `until` and `while`. 1-{MAX_REACH}.",
    )

    @model_validator(mode="after")
    def _exclusive(self) -> Self:
        """Each side of the window has one rule, and `max_lines` caps exactly `until` / `while`.

        Returns:
            The window, unchanged.

        Raises:
            ValueError: On two rules for one side, or `max_lines` missing or unused.
        """
        given = self.model_fields_set
        if "before" in given and self.from_ is not None:
            raise ValueError("`before` and `from` both set the window's start; keep one")
        downward = [k for k, v in (("until", self.until), ("while", self.while_)) if v is not None]
        if len(downward) > 1:
            raise ValueError("`until` and `while` both set the window's end; keep one")
        if downward and "after" in given:
            raise ValueError(f"`after` and `{downward[0]}` both set the window's end; keep one")
        if downward and self.max_lines is None:
            raise ValueError(
                f"`{downward[0]}` needs `max_lines`, so a window that never closes stays bounded"
            )
        if not downward and self.max_lines is not None:
            raise ValueError("`max_lines` caps `until` or `while`; without either it does nothing")
        return self
