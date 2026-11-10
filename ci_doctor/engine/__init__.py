"""The matching engine: matcher files in, evidence lines out. Pure: no I/O, no config.

Layered bottom-up, each layer importing only the ones below it (pinned by
`tests/test_layout.py`):

- `spec/`: what a matcher file may say, validated on load.
- `run/`: matchers × lines → windows.
- `select/`: windows → evidence (merge, shed, render, fired) and the token budget.

This module is the only import surface: code outside `engine/` imports from
`ci_doctor.engine`, never from a submodule.
"""

from ci_doctor.engine.select.budget import estimate_tokens, fit
from ci_doctor.engine.select.extract import extract
from ci_doctor.engine.select.fired import fired
from ci_doctor.engine.spec.legacy import MatcherConfig
from ci_doctor.engine.spec.roles import ROLE_RANK, Role
from ci_doctor.engine.spec.text import Description, Tag

__all__ = [
    "ROLE_RANK",
    "Description",
    "MatcherConfig",
    "Role",
    "Tag",
    "estimate_tokens",
    "extract",
    "fired",
    "fit",
]
