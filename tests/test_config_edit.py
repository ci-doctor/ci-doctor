"""`edit_matchers` rewrites the `matchers:` block and nothing else."""

import pytest
import yaml

from ci_doctor.errors import SetupError
from ci_doctor.matchers import config_edit
from ci_doctor.matchers.config_edit import edit_matchers


def _add(ref):
    return lambda m: m.setdefault("include", []).append(ref)


def test_comments_around_the_block_survive_byte_for_byte(tmp_path):
    """Comments around the block survive byte for byte."""
    path = tmp_path / "c.yml"
    path.write_text(
        "# top\nci: github   # trailing\n\nmatchers:\n  include: [a/b]  # lost\n\n"
        "# about llm\nllm:\n  model: x  # keep\n# end\n"
    )
    edit_matchers(path, _add("a/c"))
    assert path.read_text() == (
        "# top\nci: github   # trailing\n\nmatchers:\n  include:\n  - a/b\n  - a/c\n\n"
        "# about llm\nllm:\n  model: x  # keep\n# end\n"
    )


def test_a_missing_matchers_key_is_appended(tmp_path):
    """A missing matchers key is appended."""
    path = tmp_path / "c.yml"
    path.write_text("ci: github  # c")
    edit_matchers(path, _add("a/b"))
    assert path.read_text() == "ci: github  # c\nmatchers:\n  include:\n  - a/b\n"


def test_a_missing_file_is_created(tmp_path):
    """A missing file is created."""
    path = tmp_path / "new.yml"
    edit_matchers(path, _add("a/b"))
    assert yaml.safe_load(path.read_text()) == {"matchers": {"include": ["a/b"]}}


def test_flow_style_block_is_replaced(tmp_path):
    """Flow style block is replaced."""
    path = tmp_path / "c.yml"
    path.write_text("matchers: {include: [a/b]}\nci: github\n")
    edit_matchers(path, _add("a/c"))
    assert yaml.safe_load(path.read_text()) == {"matchers": {"include": ["a/b", "a/c"]}, "ci": "github"}
    assert path.read_text().endswith("ci: github\n")


def test_matchers_as_the_last_key(tmp_path):
    """Matchers as the last key."""
    path = tmp_path / "c.yml"
    path.write_text("ci: github\nmatchers:\n  include: [a/b]\n")
    edit_matchers(path, _add("a/c"))
    assert path.read_text() == "ci: github\nmatchers:\n  include:\n  - a/b\n  - a/c\n"


def test_a_mis_splice_is_caught_and_the_file_left_alone(tmp_path, monkeypatch):
    """A mis splice is caught and the file left alone."""
    path = tmp_path / "c.yml"
    original = "ci: github\nmatchers:\n  include: [a/b]\nllm:\n  model: x\n"
    path.write_text(original)
    monkeypatch.setattr(config_edit, "_splice", lambda text, dumped: dumped)  # drops everything else
    with pytest.raises(SetupError, match="more than `matchers`"):
        edit_matchers(path, _add("a/c"))
    assert path.read_text() == original


def test_an_edit_that_breaks_the_schema_is_refused(tmp_path):
    """An edit that breaks the schema is refused."""
    path = tmp_path / "c.yml"
    path.write_text("ci: github\n")
    with pytest.raises(SetupError, match="invalid config"):
        edit_matchers(path, _add("not-qualified"))
    assert path.read_text() == "ci: github\n"


def test_a_non_mapping_file_is_refused(tmp_path):
    """A non mapping file is refused."""
    path = tmp_path / "c.yml"
    path.write_text("- a\n- b\n")
    with pytest.raises(SetupError, match="mapping"):
        edit_matchers(path, _add("a/b"))
