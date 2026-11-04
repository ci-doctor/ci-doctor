"""The effective config as text: YAML, a diff against the defaults, the JSON Schema."""

import difflib
import json

import yaml

from ci_doctor.config.schemas import Config


def as_yaml(cfg: Config) -> str:
    """Dump a validated config back to YAML.

    Args:
        cfg: A :class:`~ci_doctor.config.schemas.Config`.

    Returns:
        YAML text in schema field order, so two dumps diff cleanly.
    """
    return yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def diff_lines(before: str, after: str) -> list[str]:
    """Unified diff of the shipped defaults against the effective config.

    Args:
        before: YAML dump of the shipped defaults.
        after: YAML dump of the effective config.

    Returns:
        Diff lines without the ``---``/``+++`` file headers (they carry no
        information here) and without trailing newlines; empty when identical.
    """
    diff = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), n=1)
    return [line.rstrip("\n") for line in diff][2:]


def schema_json() -> str:
    """Render the config JSON Schema.

    Published as a release asset so editors can complete and validate a
    ``.ci-doctor.yml`` against the exact version in use.

    Returns:
        The pretty-printed JSON Schema document.
    """
    return json.dumps(Config.model_json_schema(), indent=2)
