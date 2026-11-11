"""The text fields of a matcher file: tags and the prompt-bound description."""

from typing import Annotated

from pydantic import AfterValidator, StringConstraints, ValidationInfo

from ci_doctor.engine.spec.limits import DESCRIPTION_CHARS


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


#: A tag: lowercase, digits and dashes.
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+$")]
#: A description: one line of printable ASCII, safe to put in a prompt.
Description = Annotated[str, AfterValidator(_description)]
