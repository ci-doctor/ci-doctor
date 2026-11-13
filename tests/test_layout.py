"""The package layering GUIDELINES §2 describes: `cli/` on top, nothing importing it; `engine/` pure."""

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


def test_the_mcp_surface_has_no_terminal_in_it_and_owns_the_sdk():
    """`mcp/` is a surface like `cli/`, without its terminal; nothing else imports the MCP SDK."""
    assert _offenders((PACKAGE / "mcp").rglob("*.py"), ("typer", "click", "rich", "ci_doctor.cli")) == {}
    outside = [p for p in PACKAGE.rglob("*.py") if PACKAGE / "mcp" not in p.parents]
    assert _offenders(outside, ("mcp",)) == {}


def test_only_the_mcp_command_imports_the_mcp_surface():
    """A tool calls `service/`; a service reaching back into `mcp/` would turn that around."""
    others = [
        p for p in PACKAGE.rglob("*.py") if PACKAGE / "mcp" not in p.parents and p != PACKAGE / "cli/mcp.py"
    ]
    assert _offenders(others, ("ci_doctor.mcp",)) == {}


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


ENGINE = PACKAGE / "engine"
#: `engine/`'s layers, bottom first: each imports only the ones before it.
LAYERS = ("spec", "compile", "run", "select")


def _layer(module: str) -> int:
    """A dotted `ci_doctor.engine.*` module's layer; modules beside the layers sit under them all."""
    part = module.split(".")[2] if module.count(".") >= 2 else ""
    return LAYERS.index(part) if part in LAYERS else -1


def test_the_engine_imports_only_lib_pydantic_and_the_stdlib():
    """The engine is pure: no config, no providers, no I/O dependency to drift under it."""
    import sys

    allowed = {"ci_doctor.lib", "ci_doctor.engine", "pydantic", *sys.stdlib_module_names}
    bad = {
        str(p.relative_to(PACKAGE)): sorted(out)
        for p in ENGINE.rglob("*.py")
        if (out := {m for m in _imports(p) if not any(m == a or m.startswith(f"{a}.") for a in allowed)})
    }
    assert bad == {}


def test_engine_layers_import_only_downwards():
    """`spec` <- `compile` <- `run` <- `select`: a layer never reaches above itself."""
    bad = {}
    for p in ENGINE.rglob("*.py"):
        rel = p.relative_to(ENGINE)
        if rel == Path("__init__.py"):
            continue  # the surface re-exports every layer
        own = LAYERS.index(rel.parts[0]) if rel.parts[0] in LAYERS else -1
        if up := sorted(m for m in _imports(p) if m.startswith("ci_doctor.engine.") and _layer(m) > own):
            bad[str(rel)] = up
    assert bad == {}


def test_outside_code_imports_the_engine_only_through_its_surface():
    """`ci_doctor.engine` is the contract; its submodules can move without touching callers."""
    bad = {
        str(p.relative_to(PACKAGE)): deep
        for p in PACKAGE.rglob("*.py")
        if ENGINE not in p.parents
        and (deep := sorted(m for m in _imports(p) if m.startswith("ci_doctor.engine.")))
    }
    assert bad == {}


def test_config_imports_nothing_from_core():
    """`core` reads the config; the config reaching back into `core` was an import cycle."""
    assert _offenders((PACKAGE / "config").rglob("*.py"), ("ci_doctor.core",)) == {}


def test_lib_is_a_leaf():
    """Every package may use `lib/`, so it may use none of them back."""
    bad = {
        str(p.relative_to(PACKAGE)): out
        for p in (PACKAGE / "lib").rglob("*.py")
        if (out := sorted(m for m in _imports(p) if m.startswith("ci_doctor.") and m != "ci_doctor.errors"))
    }
    assert bad == {}
