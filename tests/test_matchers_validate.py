"""Registry file rules (E11 §1b): every field cap, the bare id, and safe loading.

Each rule gets a failing case built by editing a freshly written registry, so the
passing case is that registry itself (`write_registry` indexes it, which proves it
builds).
"""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import Index, check_relative_path
from ci_doctor.matchers.validate import contained, load_bundle, load_matcher
from tests import support

PYTEST = "matchers/python/pytest/matcher.yaml"
RLO, EM_DASH, ZWSP = chr(0x202E), chr(0x2014), chr(0x200B)


@pytest.fixture
def registry(tmp_path):
    """A freshly written two-matcher registry, free to edit."""
    return support.py_registry(tmp_path / "registry")


def _edit(root: Path, rel: str, **changes) -> Path:
    """Rewrite one YAML file in the copy; a `None` value deletes the key."""
    path = root / rel
    data = yaml.safe_load(path.read_text())
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_the_registry_matcher_loads_as_written(registry):
    """The passing case every failing case below is an edit of."""
    m = load_matcher(registry / PYTEST, author=True)
    assert m.id == "pytest"
    assert m.description.startswith("pytest's")


@pytest.mark.parametrize(
    ("description", "why"),
    [
        (f"pytest failures {RLO} right-to-left override hides text", "printable ASCII"),
        (f"pytest failures {EM_DASH} with an em dash in the middle", "printable ASCII"),
        (f"pytest zero-width{ZWSP}space hides a word", "printable ASCII"),
        ("x" * 19, "19 characters, outside 20-250"),
        ("x" * 251, "251 characters, outside 20-250"),
        ("pytest failures \x1b[31m with a terminal escape", "printable ASCII"),
    ],
)
def test_a_description_must_be_safe_prompt_text(registry, description, why):
    """A description is prompt text: one line of printable ASCII, bounded."""
    path = _edit(registry, PYTEST, description=description)
    with pytest.raises(SetupError, match=why):
        load_matcher(path)


def test_a_description_is_required_in_a_registry(registry):
    """Optional in a config, required in a registry: it is all the LLM learns about the tool."""
    with pytest.raises(SetupError, match=r"description\s+Field required"):
        load_matcher(_edit(registry, PYTEST, description=None))


def test_a_tag_is_required_in_a_registry(registry):
    """The first tag names the ecosystem directory, so a registry matcher needs one."""
    with pytest.raises(SetupError, match=r"classification\s+Field required"):
        load_matcher(_edit(registry, PYTEST, classification=None))
    with pytest.raises(SetupError, match=r"classification\s+List should have at least 1 item"):
        load_matcher(_edit(registry, PYTEST, classification=[]))


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ({"start": "^=+ FAILURES " + "x" * 488}, "501 characters, over 500"),
        ({"start": "(a|b|c){400}"}, "1204 RE2 instructions, over 1000"),
        ({"start": ".*"}, "matches an empty line"),
        ({"end": "^"}, "matches an empty line"),
        ({"start": None, "end": None, "pattern": "x*"}, "matches an empty line"),
        ({"start": None, "end": None, "pattern": "FAIL", "before": -1}, "greater than or equal to 0"),
        ({"start": None, "end": None, "pattern": "FAIL", "after": 101}, "less than or equal to 100"),
        ({"exclude": [f"skip{i}" for i in range(21)]}, "at most 20 items"),
        ({"classification": ["python", "a", "b", "c", "d", "e"]}, "at most 5 items"),
    ],
)
def test_a_matcher_field_out_of_bounds_is_rejected(registry, changes, why):
    """Every cost cap: pattern length, RE2 program size, empty-line anchors, windows, lists."""
    with pytest.raises(SetupError, match=why):
        load_matcher(_edit(registry, PYTEST, **changes))


def test_the_bare_id_must_match_its_directory(registry):
    """Author time: the name users type is the directory name."""
    with pytest.raises(SetupError, match="must equal its directory name 'pytest'"):
        load_matcher(_edit(registry, PYTEST, id="pytest_two"), author=True)


def test_a_slash_in_the_file_id_is_rejected_even_though_the_model_allows_it(registry):
    """`alias/id` is what ci-doctor makes of it at load; the file holds the bare name."""
    with pytest.raises(SetupError, match=r"id\s+String should match pattern"):
        load_matcher(_edit(registry, PYTEST, id="acme/pytest"))


def test_tags_are_free_the_folder_is_the_ecosystem(registry):
    """Tags are for search: no tag has to name the directory the matcher sits in."""
    assert load_matcher(_edit(registry, PYTEST, classification=["test"]), author=True).classification == [
        "test"
    ]


def test_a_brand_new_ecosystem_is_accepted(registry):
    """Ecosystems are open: a new one needs nothing but a directory."""
    shutil.move(registry / "matchers/python/pytest", registry / "matchers/zig/pytest")
    path = _edit(registry, "matchers/zig/pytest/matcher.yaml", classification=["zig", "test"])
    assert load_matcher(path, author=True).classification[0] == "zig"


def test_yaml_anchors_and_aliases_are_rejected(registry):
    """A billion-laughs bomb fits in a few lines; the loader refuses the mechanism."""
    path = registry / PYTEST
    path.write_text(path.read_text() + "exclude: &skips ['^skip']\n" + "x: *skips\n")
    with pytest.raises(SetupError, match="anchors and aliases are not allowed"):
        load_matcher(path)


def test_a_symlink_cannot_lead_outside_the_registry(registry, tmp_path):
    """Containment is checked on the real path, not the index path's text."""
    secret = tmp_path / "secret.yaml"
    secret.write_text("id: secret\n")
    (registry / "matchers/python/evil").mkdir()
    (registry / "matchers/python/evil/matcher.yaml").symlink_to(secret)
    with pytest.raises(SetupError, match="resolves outside the registry"):
        contained(registry, "matchers/python/evil/matcher.yaml")
    assert contained(registry, PYTEST) == (registry / PYTEST).resolve()


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd.yaml",
        "../other/index.yaml",
        "a/../../b.yaml",
        "a\\b.yaml",
        "https://x/y.yaml",
        "a/b.yml",
        "%2e%2e/%2e%2e/x.yaml",
        "a/x.yaml?.yaml",
        "a/x.yaml#.yaml",
    ],
)
def test_an_index_path_must_stay_relative(path):
    """A hostile index cannot point at an absolute path, a parent, or another host."""
    with pytest.raises(ValueError, match="relative POSIX path"):
        check_relative_path(path)


def test_an_index_rejects_unknown_keys_and_versions():
    """Format changes fail loudly rather than being half-read."""
    entry = {"path": "bundles/a.yaml", "sha256": "0" * 64, "description": "x"}
    Index.model_validate({"schema": 1, "bundles": {"ab": entry}})
    with pytest.raises(ValidationError):
        Index.model_validate({"schema": 2, "bundles": {"ab": entry}})
    with pytest.raises(ValidationError):
        Index.model_validate({"schema": 1, "extra": True})


def test_a_bundle_rejects_duplicates_and_a_bad_id(registry):
    """A bundle is a hand-written list; a duplicate or a bad name is always a mistake."""
    path = registry / "bundles/python.yaml"
    path.write_text(
        "id: python\ndescription: Python test failures and exceptions.\ninclude: [pytest, pytest]\n"
    )
    with pytest.raises(SetupError, match="more than once"):
        load_bundle(path)
    path.write_text("id: Python\ndescription: Python test failures and exceptions.\ninclude: [pytest]\n")
    with pytest.raises(SetupError, match="id"):
        load_bundle(path)


def test_a_one_letter_ecosystem_names_its_bundle(registry):
    """Ecosystems are open, `c` included, and each gets a bundle named after it."""
    path = registry / "bundles/c.yaml"
    path.write_text("id: c\ndescription: C and C++ compile and link errors.\ninclude: [pytest]\n")
    assert load_bundle(path, author=True).id == "c"
