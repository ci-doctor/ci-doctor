"""`matchers:`: which registries to read matchers from, and what to include."""

import re
from typing import Annotated

from pydantic import AfterValidator, Field

from ci_doctor.config.schemas._base import Strict

#: A registry name: what users type after the `/` in `alias/name`, and the matcher's
#: directory name. One character is enough: a bundle is named after its ecosystem tag,
#: and `c` is one. Unanchored; match it whole.
BARE_ID = r"[a-z][a-z0-9_]{0,63}"
#: A registry alias, the part before the `/` in `alias/name`.
_ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
#: A qualified matcher or bundle reference.
_REF = re.compile(rf"{_ALIAS.pattern}/{BARE_ID}")
#: A qualified output reference: `alias/matcher.output`, or `alias/matcher` for a one-output matcher.
_OUTPUT_REF = re.compile(rf"{_REF.pattern}(\.{BARE_ID})?")


def _alias(value: str) -> str:
    """Validate a registry alias.

    Args:
        value: The key under `matchers.registries`.

    Returns:
        The alias, unchanged.

    Raises:
        ValueError: If it is not a valid alias.
    """
    if not _ALIAS.fullmatch(value):
        raise ValueError(f"registry alias {value!r} must match {_ALIAS.pattern}")
    return value


def unqualified(value: str) -> str:
    """The hint for a bare name where `<registry>/<name>` was needed.

    Args:
        value: The name as written.

    Returns:
        The message.
    """
    return f"{value!r} is not a qualified reference: write `<registry>/<name>`, e.g. `official/{value}`"


def _ref(value: str) -> str:
    """Validate an `alias/name` reference, with a hint for the unqualified form.

    Args:
        value: One entry of `matchers.include`.

    Returns:
        The reference, unchanged.

    Raises:
        ValueError: If it is not `<registry>/<name>`.
    """
    if not _REF.fullmatch(value):
        raise ValueError(unqualified(value))
    return value


def _output_ref(value: str) -> str:
    """Validate an `alias/matcher.output` reference.

    Args:
        value: One entry of `matchers.exclude`.

    Returns:
        The reference, unchanged.

    Raises:
        ValueError: If it is not `<registry>/<matcher>[.<output>]`.
    """
    if not _OUTPUT_REF.fullmatch(value):
        raise ValueError(f"{value!r} is not an output reference: write `<registry>/<matcher>.<output>`")
    return value


class MatchersConfig(Strict):
    """Which registries to read matchers from, and what to load from them."""

    registries: dict[Annotated[str, AfterValidator(_alias)], str] = Field(
        default_factory=dict,
        description=(
            "Registry alias -> its folder (the one holding index.yaml): a path (relative to the "
            "config file), `file://`, `http://` or `https://`. Merged onto the defaults, so "
            "`official` stays unless you override it."
        ),
    )
    include: list[Annotated[str, AfterValidator(_ref)]] = Field(
        default_factory=list,
        description=(
            "Matchers and bundles to load, as `<registry>/<name>` (e.g. `official/python`). "
            "Replaces the default list. Browse https://matchers.cidoc.dev"
        ),
    )
    exclude: list[Annotated[str, AfterValidator(_output_ref)]] = Field(
        default_factory=list,
        description=(
            "Outputs to drop from the included matchers, as `<registry>/<matcher>.<output>` "
            "(e.g. `official/ruff.format`). Each must name an output of an included matcher."
        ),
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=300,
        description="Seconds to wait on each registry request (connect, and each read) before failing the setup.",
    )
    fetch_workers: int = Field(
        default=8,
        ge=1,
        le=32,
        description="Registry files fetched at once. Lower it for a registry that rate-limits; 1 fetches in order.",
    )
