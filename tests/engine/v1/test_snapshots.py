"""Invariant #11: a released matcher schema version never changes how it compiles.

Each `cases/*.yaml` compiles to its committed `*.plan.json`. A change that moves a
snapshot changes a released format: that needs a new schema version instead.
`CI_DOCTOR_UPDATE_SNAPSHOTS=1` rewrites them, for a case that is new.
"""

import json
import os
from pathlib import Path

import pytest
import yaml

from ci_doctor.engine import compile_plan, load, to_json

CASES = sorted((Path(__file__).parent / "cases").glob("*.yaml"))


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
def test_schema_1_compiles_to_its_frozen_plan(case):
    """The Plan, as JSON, equals the committed snapshot."""
    got = json.dumps(to_json(compile_plan([load(yaml.safe_load(case.read_text()))])), indent=2) + "\n"
    snapshot = case.with_suffix(".plan.json")
    if os.environ.get("CI_DOCTOR_UPDATE_SNAPSHOTS") == "1":
        snapshot.write_text(got)
    assert got == snapshot.read_text()
