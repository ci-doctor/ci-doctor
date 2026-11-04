"""The RE2-checked regex field, shared by every block that takes a pattern."""

from typing import Annotated

from pydantic import AfterValidator, StringConstraints, ValidationInfo

from ci_doctor.core.regex import compile_user


def _re2(pattern: str, info: ValidationInfo) -> str:
    """Reject a pattern RE2 cannot compile, at load time rather than mid-run.

    Args:
        pattern: The regex as written in the config.
        info: Validation context; on a matcher it carries the already-validated `id`,
            which names the culprit better than its index in the merged list.

    Returns:
        The pattern, unchanged.

    Raises:
        ValueError: If RE2 rejects it, naming the matcher when there is one.
    """
    try:
        compile_user(pattern)
    except ValueError as err:
        owner = info.data.get("id")
        raise ValueError(f"matcher {owner!r}: {err}" if owner else str(err)) from None
    return pattern


#: A regex field: non-empty (`''` compiles, and fires on everything or nothing) and
#: validated as RE2 when the config loads.
Re2 = Annotated[str, StringConstraints(min_length=1), AfterValidator(_re2)]
