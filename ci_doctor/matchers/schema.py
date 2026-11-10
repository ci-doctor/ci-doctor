"""The JSON Schema of a matcher file, per schema version: for editors, and the docs' field tables.

Attached to every release as `matcher-v<N>.schema.json`; a matcher file opts in with
`# yaml-language-server: $schema=<SCHEMA_ID>` on its first line.
"""

import json

from ci_doctor.engine import SCHEMAS

#: Where each version's schema is published.
SCHEMA_ID = "https://github.com/ci-doctor/ci-doctor/releases/latest/download/matcher-v{version}.schema.json"


def matcher_schema(version: int = 1) -> str:
    """The JSON Schema of a matcher file in one schema version, for editors and the docs.

    Args:
        version: The schema version.

    Returns:
        Pretty-printed JSON.

    Raises:
        ValueError: For a version this ci-doctor doesn't know.
    """
    if version not in SCHEMAS:
        raise ValueError(f"this ci-doctor knows matcher schema {', '.join(map(str, SCHEMAS))}, not {version}")
    schema = SCHEMAS[version].model_json_schema(by_alias=True)
    doc = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID.format(version=version),
    }
    return json.dumps(doc | schema, indent=2)
