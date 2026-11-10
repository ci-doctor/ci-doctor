"""The matching engine: matcher files in, evidence lines out. Pure: no I/O, no config.

Layered bottom-up, each layer importing only the ones below it (pinned by
`tests/test_layout.py`):

- `plan.py`, `errors.py`: the Plan every schema version compiles to, and the errors.
- `spec/`: what a matcher file may say, per schema version, validated on load.
- `compile/`: spec → Plan.
- `run/`: Plan × lines → windows.
- `select/`: windows → evidence (merge, shed, render, fired) and the token budget.

This module is the only import surface: code outside `engine/` imports from
`ci_doctor.engine`, never from a submodule.
"""

from ci_doctor.engine.compile import compile_plan
from ci_doctor.engine.errors import CompileError, UnknownSchema
from ci_doctor.engine.plan import Output, Plan, to_json
from ci_doctor.engine.run.windows import Window, run
from ci_doctor.engine.select.budget import estimate_tokens, fit
from ci_doctor.engine.select.evidence import select
from ci_doctor.engine.select.fired import fired
from ci_doctor.engine.spec import SCHEMAS, Spec, load, schema_of
from ci_doctor.engine.spec.legacy import MatcherConfig
from ci_doctor.engine.spec.roles import RANK, Role
from ci_doctor.engine.spec.text import Description, Tag
from ci_doctor.engine.spec.v1 import Matcher as MatcherV1

__all__ = [
    "RANK",
    "SCHEMAS",
    "CompileError",
    "Description",
    "MatcherConfig",
    "MatcherV1",
    "Output",
    "Plan",
    "Role",
    "Spec",
    "Tag",
    "UnknownSchema",
    "Window",
    "compile_plan",
    "estimate_tokens",
    "fired",
    "fit",
    "load",
    "run",
    "schema_of",
    "select",
    "to_json",
]
