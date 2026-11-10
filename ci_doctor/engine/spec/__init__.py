"""L1 · what a matcher file may say, per schema version, validated on load."""

from typing import Any

from ci_doctor.engine.errors import UnknownSchema
from ci_doctor.engine.spec.legacy import MatcherConfig
from ci_doctor.engine.spec.v1 import Matcher as MatcherV1

#: A validated matcher of any schema version this ci-doctor knows.
Spec = MatcherConfig | MatcherV1

#: Every schema version this ci-doctor compiles. A released version never changes
#: (invariant #11); a new one is a new entry here.
SCHEMAS: dict[int, type[MatcherV1]] = {1: MatcherV1}


def schema_of(data: Any) -> int | None:  # noqa: ANN401 - parsed YAML
    """The schema version a parsed matcher file declares.

    Args:
        data: The parsed file.

    Returns:
        The version, or None for the legacy format, which has no `schema` key.

    Raises:
        UnknownSchema: If it names a version this ci-doctor doesn't know.
    """
    if not isinstance(data, dict) or "schema" not in data:
        return None
    version = data["schema"]
    if isinstance(version, bool) or version not in SCHEMAS:
        raise UnknownSchema(version)
    return version


def load(data: Any, legacy: type[MatcherConfig] = MatcherConfig) -> Spec:  # noqa: ANN401 - parsed YAML
    """Validate a parsed matcher file with its schema version's model.

    Args:
        data: The parsed file.
        legacy: The model for a file with no `schema` (a registry passes its stricter one).

    Returns:
        The validated spec.

    Raises:
        UnknownSchema: For a version this ci-doctor doesn't know.
        pydantic.ValidationError: For anything else wrong with the file.
    """
    version = schema_of(data)
    model = legacy if version is None else SCHEMAS[version]
    return model.model_validate(data)
