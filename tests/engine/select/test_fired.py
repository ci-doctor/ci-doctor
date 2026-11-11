"""Fired: which packs anchor in the evidence actually sent."""

from ci_doctor.engine import estimate_tokens
from tests.engine.support import extract, rx
from tests.engine.support import fired_ids as fired


def test_fired_names_the_packs_anchored_in_the_final_lines_in_log_order():
    """Asked once, of the evidence actually sent: a shed pack is not in it."""
    lines = [f"error TS2345: bad argument {i}" for i in range(100)]
    lines += [f"  building chunk {i}" for i in range(20)]
    lines += [f"npm ERR! code ELIFECYCLE {i}" for i in range(100)]
    matchers = [rx("npm ERR!", id="npm", role="wrapper"), rx("error TS", id="tsc")]
    assert fired(lines, matchers) == ["tsc", "npm"]
    kept = extract(lines, matchers, tail_lines=0, max_tokens=estimate_tokens("\n".join(lines)) // 2)
    assert fired(kept, matchers) == ["tsc"]


def test_fired_names_a_pack_whose_anchor_is_only_context_of_another_window():
    """Its line is in the evidence, so the model should know which tool printed it."""
    lines = ["Traceback (most recent call last):", "=== FAILURES ===", "boom"]
    matchers = [
        rx("FAILURES", id="pytest", before=1),
        rx("^Traceback", id="python_traceback"),
    ]
    assert fired(extract(lines, [matchers[0]], tail_lines=0), matchers) == ["python_traceback", "pytest"]
