"""One registry `matcher.yaml`, as validated on load. Not a config key: `matchers:` selects these."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints, ValidationInfo, model_validator

from ci_doctor.config.schemas._base import Strict
from ci_doctor.config.schemas._regex import Re2
from ci_doctor.core.regex import compile_user, programsize

#: Caps on what a matcher may cost, set from the 38 that once shipped, with headroom: the
#: largest RE2 program was 109, the longest pattern 104 characters.
MAX_PATTERN_CHARS = 500
MAX_PROGRAM_SIZE = 1000
DESCRIPTION_CHARS = (20, 250)


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


def _description(text: str, info: ValidationInfo) -> str:
    """Collapse to one line, then hold it to what is safe to put in a prompt.

    A newline could forge a prompt section; a non-ASCII character could hide text
    from a reviewer (zero-width, bidi) without hiding it from the model.

    Args:
        text: The description as written.
        info: Validation context carrying the matcher's `id`.

    Returns:
        The text on one line.

    Raises:
        ValueError: On a length outside the range, or a non-printable-ASCII character.
    """
    owner = info.data.get("id")
    text = " ".join(text.split())
    low, high = DESCRIPTION_CHARS
    if not low <= len(text) <= high:
        raise ValueError(f"{owner!r}: description is {len(text)} characters, outside {low}-{high}")
    if not (text.isascii() and text.isprintable()):
        raise ValueError(f"{owner!r}: description must be printable ASCII, got {text!a}")
    return text


#: A matcher regex: RE2, and bounded in length and program size.
MatcherRe2 = Annotated[Re2, AfterValidator(_matcher_re2)]
#: A matcher's anchor (`pattern`, `start`, `end`): additionally never matches an empty line.
Anchor = Annotated[MatcherRe2, AfterValidator(_anchor)]
#: A classification tag: lowercase, digits and dashes.
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$")]
#: A description: one line of printable ASCII, safe to put in a prompt.
Description = Annotated[str, AfterValidator(_description)]
#: What a matcher's output is; ranks its windows (`extract.ROLE_RANK`).
Role = Literal["tool", "wrapper", "fallback"]


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
