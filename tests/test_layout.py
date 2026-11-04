"""The package layering GUIDELINES §2 describes: `cli/` on top, nothing importing it."""

import ast
from pathlib import Path

import ci_doctor

PACKAGE = Path(ci_doctor.__file__).parent


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _offenders(files, banned):
    return {
        str(p.relative_to(PACKAGE)): bad
        for p in files
        if (bad := sorted({m for m in _imports(p) if any(m == b or m.startswith(f"{b}.") for b in banned)}))
    }


def test_nothing_outside_cli_imports_it():
    """`cli/` calls `service/` and the rest; a module importing `cli` would turn that around."""
    files = [p for p in PACKAGE.rglob("*.py") if PACKAGE / "cli" not in p.parents]
    assert _offenders(files, ("ci_doctor.cli",)) == {}


def test_service_has_no_terminal_in_it():
    """A service is what an editor or an agent calls too: no typer, no click."""
    assert _offenders((PACKAGE / "service").rglob("*.py"), ("typer", "click")) == {}


def test_each_config_key_has_its_own_schema_module():
    """`config/schemas/` mirrors `.ci-doctor.yml`: the model for `llm:` lives in `schemas/llm.py`."""
    from pydantic import BaseModel

    from ci_doctor.config.schemas import Config

    misplaced = {
        name: field.annotation.__module__
        for name, field in Config.model_fields.items()
        if isinstance(field.annotation, type)
        and issubclass(field.annotation, BaseModel)
        and field.annotation.__module__ != f"ci_doctor.config.schemas.{name}"
    }
    assert misplaced == {}
