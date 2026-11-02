"""The docs site's phase map is generated, so it must not drift.

The site imports `src/data/phases.json` instead of a hand-written table, which
would be stale by the next commit. This pins it to what `defaults.yml` ships.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gen_docs_data import PHASES_OUT, build_phases

from ci_doctor.config.loader import load_config


def test_docs_phase_map_is_current():
    """The docs' section->phase table is the shipped map, not a copy of it."""
    assert json.loads(PHASES_OUT.read_text()) == build_phases(), (
        "docs/site/src/data/phases.json is stale — run `mise run docs:data`"
    )


def test_every_shipped_section_name_reaches_the_docs():
    """Guards the phase-block parser: a parse bug would silently drop rows."""
    shipped = load_config(environ={}).phases
    listed = {row["section"]: row["phase"] for row in build_phases()}
    assert listed == shipped
