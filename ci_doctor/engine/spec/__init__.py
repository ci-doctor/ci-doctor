"""L1 · what a matcher file may say, per schema version, validated on load."""

from typing import Any

from ci_doctor.engine.errors import UnknownSchema
from ci_doctor.engine.spec.v1 import Matcher as MatcherV1

#: A validated matcher of any schema version this ci-doctor knows.
Spec = MatcherV1

#: Every schema version this ci-doctor compiles. A released version never changes
#: (invariant #11); a new one is a new entry here.
SCHEMAS: dict[int, type[MatcherV1]] = {1: MatcherV1}


def load(data: Any) -> Spec:  # noqa: ANN401 - parsed YAML
    """Validate a parsed matcher file with its schema version's model.

    Args:
        data: The parsed file.

    Returns:
        The validated spec.

    Raises:
        UnknownSchema: When `schema` is missing or names a version this ci-doctor doesn't know.
        pydantic.ValidationError: For anything else wrong with the file.
    """
    version = data.get("schema") if isinstance(data, dict) else None
    if type(version) is not int or version not in SCHEMAS:  # not bool, and hashable
        raise UnknownSchema(version)
    return SCHEMAS[version].model_validate(data)
