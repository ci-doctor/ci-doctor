"""Today's `matcher.yaml` format, as validated on load: `pattern` or `start`/`end`.

The format every registry matcher is written in until `schema: 1` lands. Not a
config key: `matchers:` in `.ci-doctor.yml` selects these.
"""

from typing import Annotated

from pydantic import AfterValidator, Field, ValidationInfo, model_validator

from ci_doctor.engine.spec.base import Strict
from ci_doctor.engine.spec.limits import MAX_PATTERN_CHARS, MAX_PROGRAM_SIZE
from ci_doctor.engine.spec.roles import Role
from ci_doctor.engine.spec.text import Description, Tag
from ci_doctor.regex import Re2, compile_user, programsize


def _matcher_re2(pattern: str, info: ValidationInfo) -> str:
    """Bound what one matcher regex may cost on every log line.

    Args:
        pattern: The regex, already known to compile.
        info: Validation context carrying the matcher's `id`.

    Returns:
        The pattern, unchanged.

    Raises:
        ValueError: If the pattern is too long or compiles to too large a program.
    """
    owner = info.data.get("id")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"matcher {owner!r}: pattern is {len(pattern)} characters, over {MAX_PATTERN_CHARS}")
    if (size := programsize(pattern)) > MAX_PROGRAM_SIZE:
        raise ValueError(
            f"matcher {owner!r}: {pattern!r} compiles to {size} RE2 instructions, over {MAX_PROGRAM_SIZE}"
        )
    return pattern


def _anchor(pattern: str, info: ValidationInfo) -> str:
    """Reject an anchor that matches an empty line: `.*`, `^`, `x*` fire on every line.

    Args:
        pattern: The anchor regex.
        info: Validation context carrying the matcher's `id`.

    Returns:
        The pattern, unchanged.

    Raises:
        ValueError: If the pattern matches the empty string.
    """
    if compile_user(pattern).search("") is not None:
        raise ValueError(
            f"matcher {info.data.get('id')!r}: {pattern!r} matches an empty line, so it fires everywhere"
        )
    return pattern


#: A matcher regex: RE2, and bounded in length and program size.
MatcherRe2 = Annotated[Re2, AfterValidator(_matcher_re2)]
#: A matcher's anchor (`pattern`, `start`, `end`): additionally never matches an empty line.
Anchor = Annotated[MatcherRe2, AfterValidator(_anchor)]


class MatcherConfig(Strict):
    """One evidence matcher: the log window to pull around a recognised failure.

    Exactly one form: ``pattern`` with ``before``/``after`` (one anchor line plus
    context), or ``start``/``end`` (a bounded block).
    """

    id: str = Field(
        pattern=r"^[A-Za-z0-9_./-]+$",
        description="Matcher id: bare in a registry file (`pytest`), `alias/id` once loaded (`official/pytest`).",
    )
    role: Role = Field(
        "tool",
        description=(
            "How the window ranks under budget pressure: `tool` (the failing tool's own output) "
            "outranks `wrapper` (npm, make, gradle: reporting that a child process failed), "
            "which outranks `fallback`. Within a role the later window wins."
        ),
    )
    classification: list[Tag] = Field(
        default_factory=list,
        max_length=5,
        description="Up to 5 tags describing the pack, ecosystem first (`[python, test]`). Metadata only.",
    )
    description: Description | None = Field(
        None,
        description=(
            "Shown to the LLM beside the windows this pack selected: what the tool is and what "
            "its failure means. The model knows public tools, not your in-house ones. One line of "
            "20-250 printable ASCII characters."
        ),
    )
    start: Anchor | None = Field(None, description="Regex (RE2) opening a windowed matcher.")
    end: Anchor | None = Field(None, description="Regex (RE2) closing a windowed matcher.")
    pattern: Anchor | None = Field(None, description="Regex (RE2) anchoring a single-line matcher.")
    exclude: list[MatcherRe2] = Field(
        default_factory=list,
        max_length=20,
        description="Up to 20 regexes (RE2) that disqualify an anchor line (`pattern`/`start`); RE2 has no lookaround.",
    )
    before: int = Field(0, ge=0, le=100, description="Lines of context kept above a `pattern` hit, 0-100.")
    after: int = Field(0, ge=0, le=100, description="Lines of context kept below a `pattern` hit, 0-100.")

    @model_validator(mode="after")
    def _one_form(self) -> "MatcherConfig":
        """Require exactly one of `pattern`, or `start` and `end` together.

        Returns:
            The matcher, unchanged.

        Raises:
            ValueError: On neither form, both, or half a block — each a matcher
                that silently never fires or fires on the wrong thing.
        """
        line = self.pattern is not None and self.start is None and self.end is None
        block = self.pattern is None and self.start is not None and self.end is not None
        if not (line or block):
            raise ValueError(f"matcher {self.id!r}: set exactly one of `pattern`, or `start` and `end`")
        return self
