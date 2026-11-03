"""`ci-doctor matchers index`: a deterministic index, `--check`, and the cross-file rules."""

import shutil
import sys
from pathlib import Path

import pytest
import yaml

from ci_doctor import cli
from ci_doctor.errors import SetupError
from ci_doctor.matchers.index import build_index, write_index
from tests import support

PYTEST = "matchers/python/pytest/matcher.yaml"


@pytest.fixture
def registry(tmp_path):
    """A freshly written two-matcher registry, free to edit."""
    return support.py_registry(tmp_path / "registry")


def _bundle(root: Path, name: str, include: list[str]) -> None:
    """Write `bundles/<name>.yaml`."""
    body = {"id": name, "description": f"The {name} bundle, for index tests.", "include": include}
    (root / "bundles" / f"{name}.yaml").write_text(yaml.safe_dump(body))


def _main(monkeypatch, *argv) -> int:
    """Run the real entry point and return its exit code."""
    monkeypatch.setattr(sys, "argv", ["ci-doctor", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def test_the_index_is_deterministic(registry):
    """Same tree, same bytes: the property that makes merge conflicts a rerun."""
    assert build_index(registry) == build_index(registry)
    assert build_index(registry)[0] == (registry / "index.yaml").read_text()


def test_check_fails_on_a_stale_index_and_write_fixes_it(registry):
    """Editing a matcher without regenerating is caught; regenerating fixes it."""
    path = registry / PYTEST
    path.write_text(path.read_text().replace("short summary.", "short test summary line."))
    with pytest.raises(SetupError, match="index.yaml is out of date"):
        write_index(registry, check=True)
    write_index(registry)
    write_index(registry, check=True)


def test_the_cli_exits_one_on_a_stale_index_and_zero_when_current(registry, monkeypatch):
    """The registry's CI step goes red on a stale index (invariant #3's one exception)."""
    assert _main(monkeypatch, "matchers", "index", str(registry), "--check") == 0
    (registry / "index.yaml").write_text("schema: 1\n")
    assert _main(monkeypatch, "matchers", "index", str(registry), "--check") == 1


@pytest.mark.parametrize(
    ("setup", "why"),
    [
        (lambda r: _bundle(r, "pytest", ["python_traceback"]), "already a matcher"),
        (lambda r: _bundle(r, "go", ["go_test"]), "unknown names \\['go_test'\\]"),
        (lambda r: _bundle(r, "python", ["pytest", "all"]), "bundle cycle"),
        (
            lambda r: [_bundle(r, f"b{i}", [f"b{i + 1}"]) for i in range(5)] + [_bundle(r, "b5", ["pytest"])],
            "nests 6 deep, over 5",
        ),
        (
            lambda r: (r / PYTEST).write_text((r / PYTEST).read_text().replace("FAILURES", "(?=FAILURES)")),
            "not valid RE2",
        ),
        (
            lambda r: (r / PYTEST).write_text((r / PYTEST).read_text().replace("id: pytest", "id: pytest_x")),
            "must equal its directory name",
        ),
        (
            lambda r: (r / PYTEST).write_text(
                (r / PYTEST).read_text().replace("[python, test]", "[js, test]")
            ),
            "must equal its ecosystem directory",
        ),
    ],
)
def test_an_invalid_registry_is_rejected(registry, setup, why):
    """Every cross-file rule, and a broken file, stop the index from being written."""
    setup(registry)
    with pytest.raises(SetupError, match=why):
        build_index(registry)


def test_a_matcher_name_cannot_be_used_twice(registry):
    """Names are unique across the registry, whatever ecosystem directory they sit in."""
    shutil.copytree(registry / "matchers/python/pytest", registry / "matchers/js/pytest")
    copy = registry / "matchers/js/pytest/matcher.yaml"
    copy.write_text(copy.read_text().replace("[python, test]", "[js, test]"))
    with pytest.raises(SetupError, match="used twice"):
        build_index(registry)
