"""Merging: adjacent windows fuse and keep the highest rank."""

from tests.engine.support import budgeted, extract, rx


def test_overlapping_windows_merge_to_contiguous():
    """Adjacent windows merge, so no elision marker splits contiguous output."""
    lines = ["l0", "hit1", "l2", "l3", "hit2", "l5"]
    out = extract(lines, [rx("hit", before=1, after=1)], tail_lines=0)
    assert out == lines  # two adjacent windows merged; nothing elided


def test_a_merged_window_keeps_the_higher_role():
    """A tool window touching a fallback window is not demoted by the merge."""
    lines = ["ERROR first", "error TS1: cause"]
    lines += [f"  pad {i}" for i in range(20)]
    lines += [f"npm ERR! {i}" for i in range(30)]
    matchers = [
        rx("^ERROR", role="fallback"),
        rx("error TS"),
        rx("npm ERR!", role="wrapper"),
    ]
    out = budgeted(lines, matchers)
    assert "ERROR first" in out and "error TS1: cause" in out
    assert "npm ERR!" not in out
