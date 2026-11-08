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


def test_each_command_module_holds_one_command():
    """`cli/<group…>/<name>.py` is one `Command` subclass named `name`; shared code lives in `_lib/`."""
    import importlib
    import inspect

    from ci_doctor.cli._lib.command import Command

    cli = PACKAGE / "cli"
    found = {}
    for path in cli.rglob("*.py"):
        if "_lib" in path.parts or path.name.startswith("__"):
            continue
        module = importlib.import_module(
            "ci_doctor." + ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
        )
        own = [
            c
            for _, c in inspect.getmembers(module, inspect.isclass)
            if issubclass(c, Command) and c.__module__ == module.__name__
        ]
        found[str(path.relative_to(cli))] = [(c.name, bool(inspect.getdoc(c))) for c in own]
    assert found and all(v == [(Path(k).stem, True)] for k, v in found.items()), found
