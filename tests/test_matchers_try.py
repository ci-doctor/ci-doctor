"""`ci-doctor matchers try` and `matchers schema`: the real engine on one log, and the matcher JSON Schema."""

import json
import sys
from pathlib import Path

import pytest

from ci_doctor import cli
from ci_doctor.engine import load
from ci_doctor.matchers.schema import matcher_schema
from ci_doctor.matchers.try_ import try_matcher
from tests.support import gh_log

RUFF = Path(__file__).parent / "engine" / "v1" / "cases" / "ruff.yaml"
LOG = [
    "noise",
    "src/a.py:1:2: F401 unused",
    "E501 Line too long",
    "  --> src/b.py:3:4",
    "Would reformat: src/c.py",
]


def _main(monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["ci-doctor", "--plain", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code, capsys.readouterr().out


def test_try_marks_anchors_and_names_the_variants(tmp_path, monkeypatch, capsys):
    """Each output, the variants that fired, `>` on every anchor, and the gaps announced."""
    log = tmp_path / "job.log"
    log.write_text("\n".join(LOG) + "\n")
    code, out = _main(monkeypatch, capsys, "matchers", "try", str(RUFF), str(log))
    assert code == 0
    assert "ruff.check (concise, full) [tool]" in out
    assert "> src/a.py:1:2: F401 unused" in out
    assert "ruff.format [gate]\n  … [4 lines elided] …\n> Would reformat: src/c.py" in out


def test_try_strips_the_framing_its_name_declares(tmp_path):
    """A `.github.log` is read as GitHub's, exactly like a fixture."""
    import yaml

    spec = load(yaml.safe_load(RUFF.read_text()))
    raw = gh_log([("Run ruff check", ["src/a.py:1:2: F401 unused"])])
    [check, _] = try_matcher(spec, raw, "github")
    assert "> src/a.py:1:2: F401 unused" in check.lines
    [plain_check, _] = try_matcher(spec, raw, "plain")
    assert not any(line.startswith("> src/a.py") for line in plain_check.lines)  # timestamps stay, `^` misses


def test_try_with_nothing_matching_says_so_and_exits_zero(tmp_path, monkeypatch, capsys):
    """Not matching is an answer, not an error."""
    log = tmp_path / "quiet.log"
    log.write_text("all good\n")
    code, out = _main(monkeypatch, capsys, "matchers", "try", str(RUFF), str(log))
    assert (code, "nothing matched" in out) == (0, True)


def test_try_on_a_missing_log_exits_one(tmp_path, monkeypatch, capsys):
    """A setup problem is a setup error."""
    code, _ = _main(monkeypatch, capsys, "matchers", "try", str(RUFF), str(tmp_path / "nope.log"))
    assert code == 1


def test_try_reads_a_matcher_from_a_registry_by_ref(tmp_path, monkeypatch, capsys):
    """`<registry>/<name>` fetches through the configured registry, like `info`."""
    from tests import support

    reg = support.write_registry(
        tmp_path / "reg",
        {"ruff": {k: v for k, v in __import__("yaml").safe_load(RUFF.read_text()).items() if k != "id"}},
    )
    cfg = tmp_path / "cfg.yml"
    cfg.write_text(f"matchers:\n  registries:\n    fx: {reg}\n")
    log = tmp_path / "job.log"
    log.write_text("\n".join(LOG) + "\n")
    monkeypatch.setattr(
        sys, "argv", ["ci-doctor", "--plain", "-f", str(cfg), "matchers", "try", "fx/ruff", str(log)]
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "ruff.check (concise, full) [tool]" in capsys.readouterr().out


def test_the_matcher_schema_is_json_schema_with_its_id():
    """What editors read: by alias (`schema`, `from`, `while`), with a stable `$id`."""
    doc = json.loads(matcher_schema(1))
    assert doc["$id"].endswith("/matcher-v1.schema.json")
    assert {"schema", "id", "description", "tags", "outputs", "match"} <= set(doc["properties"])
    assert {"from", "while", "until", "max_lines"} <= set(doc["$defs"]["Window"]["properties"])


def test_an_unknown_schema_version_exits_one(monkeypatch, capsys):
    """Asking for a version this ci-doctor doesn't know is a setup error."""
    code, _ = _main(monkeypatch, capsys, "matchers", "schema", "--version", "9")
    assert code == 1
