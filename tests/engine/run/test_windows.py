"""Anchors: the window each matcher form keeps around a hit, and what `exclude` filters."""

from tests.engine.support import extract, matcher


def test_anchored_window_context():
    """A pattern matcher keeps its `before`/`after` context lines."""
    lines = ["a", "b", "c", "BOOM error here", "d", "e", "f"]
    out = extract(lines, [matcher(pattern="BOOM", before=1, after=1)], tail_lines=0)
    assert "c\nBOOM error here\nd" in "\n".join(out)
    assert "elided" in out[0]  # head before the window is elided visibly


def test_start_end_block():
    """A start/end matcher captures the whole block and elides both sides."""
    lines = ["pre", "=== FAILURES ===", "detail1", "detail2", "=== short test summary ===", "after"]
    out = extract(lines, [matcher(id="pytest", start="=+ FAILURES", end="short test summary")], tail_lines=0)
    assert "=== FAILURES ===\ndetail1\ndetail2\n=== short test summary ===" in "\n".join(out)
    assert out[0].startswith("… [")  # "pre" elided
    assert out[-1].startswith("… [")  # "after" elided


def test_exclude_disqualifies_an_anchor_line():
    """`exclude` is RE2's stand-in for a negative lookahead."""
    lines = ["FAIL src/cart.test.ts", "FAIL\tacme/cart\t0.038s"]
    m = matcher(pattern=r"^FAIL\s+\S+", exclude=[r"\d+\.\d+s$"])
    out = extract(lines, [m], tail_lines=0)
    assert "FAIL src/cart.test.ts" in out
    assert "FAIL\tacme/cart\t0.038s" not in out


def test_exclude_does_not_apply_to_a_blocks_end():
    """Only anchors are filtered: a block still closes on an `end` line that `exclude` matches."""
    lines = ["=== FAILURES ===", "detail", "=== short test summary ===", "after"]
    m = matcher(start="FAILURES", end="short test summary", exclude=["short"])
    out = "\n".join(extract(lines, [m], tail_lines=0))
    assert "=== short test summary ===" in out
    assert "after" not in out.replace("elided", "")
