"""Rendering: every gap in the evidence is announced, never a silent cut."""

from ci_doctor.engine import extract


def test_tail_window_only():
    """With no matchers, the tail window carries the evidence."""
    lines = [f"line{i}" for i in range(200)]
    out = extract(lines, [], tail_lines=10)
    assert out[0].startswith("… [190 lines elided]")
    assert out[-1] == "line199"
    assert "line195" in out
