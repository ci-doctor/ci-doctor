"""The JSON Schema of a matcher file, per schema version: for editors, and the docs' field tables.

Every known version is attached to each release as `matcher-v<N>.schema.json`; a matcher file opts in with
`# yaml-language-server: $schema=<SCHEMA_ID>` on its first line.
"""

import json
from pathlib import Path

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


def write_schemas(out: Path) -> list[Path]:
    """Write `matcher-v<N>.schema.json` for every schema version this ci-doctor knows.

    Args:
        out: The directory; created if missing.

    Returns:
        The files written, by version.
    """
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / f"matcher-v{v}.schema.json" for v in sorted(SCHEMAS)]
    for v, path in zip(sorted(SCHEMAS), paths, strict=True):
        path.write_text(matcher_schema(v) + "\n", encoding="utf-8")
    return paths
