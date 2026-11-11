"""Fired: which outputs anchor in the evidence actually sent."""

from ci_doctor.engine import estimate_tokens
from tests.engine.support import extract, rx, v1
from tests.engine.support import fired_ids as fired


def test_fired_names_the_outputs_anchored_in_the_final_lines_in_log_order():
    """Asked of the evidence actually sent: a shed output is not in it."""
    lines = [f"error TS2345: bad argument {i}" for i in range(100)]
    lines += [f"  building chunk {i}" for i in range(20)]
    lines += [f"npm ERR! code ELIFECYCLE {i}" for i in range(100)]
    matchers = [rx("npm ERR!", id="npm", role="wrapper"), rx("error TS", id="tsc")]
    assert fired(lines, matchers) == ["tsc", "npm"]
    assert fired(lines, matchers, max_tokens=estimate_tokens("\n".join(lines)) // 2) == ["tsc"]


def test_fired_names_an_output_whose_anchor_is_only_context_of_another_window():
    """Its line is in the evidence, so the model should know which tool printed it."""
    lines = ["Traceback (most recent call last):", "=== FAILURES ===", "boom"]
    matchers = [rx("FAILURES", id="pytest", before=1), rx("^Traceback", id="python_traceback")]
    assert fired(lines, matchers) == ["python_traceback", "pytest"]


def test_an_anchor_whose_relation_context_was_elided_still_fired():
    """The relation held in the full log; eliding its context must not unname the output."""
    lines = ["HEAD", *(["pad"] * 5), "boom"]
    m = v1(match={"all": [{"regex": "boom"}, {"preceded_by": {"regex": "HEAD", "within": 10}}]})
    assert "HEAD" not in extract(lines, [m], tail_lines=0)
    assert fired(lines, [m]) == ["x"]
