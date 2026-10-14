"""RE2 for user-supplied regexes: what it rejects, and the one-pass pattern set."""

import pytest

from ci_doctor.core.regex import compile_user, pattern_set


@pytest.mark.parametrize(
    "pattern,reason",
    [
        ("(?=x)", "invalid perl operator"),  # lookahead
        ("(?<!x)y", "invalid perl operator"),  # lookbehind
        (r"(a)\1", "invalid escape sequence"),  # backreference
        (r"a\Z", "invalid escape sequence"),  # RE2 spells it \z
        ("a{1001}", "invalid repetition size"),
    ],
)
def test_compile_user_rejects_what_re2_cannot_run_in_linear_time(pattern, reason):
    """Each rejection names the pattern and RE2's reason, decoded from bytes."""
    with pytest.raises(ValueError, match=reason) as err:
        compile_user(pattern)
    assert repr(pattern) in str(err.value)


def test_shorthand_classes_are_ascii_only():
    r"""Pins the one silent difference from `re`: \d is [0-9], not every Unicode digit."""
    assert compile_user(r"\d").search("7")
    assert compile_user(r"\d").search("٣") is None
    assert compile_user(r"\p{Nd}").search("٣")


def test_pattern_set_reports_every_pattern_that_hits():
    """One pass returns the index of each matching pattern, and nothing for a miss."""
    match = pattern_set(["^ERROR", "failed", "^WARN"])
    assert sorted(match("ERROR: build failed")) == [0, 1]
    assert match("all good") == []


def test_empty_pattern_set_matches_nothing():
    """No patterns (the shipped `noise_patterns` can be emptied) is not an error."""
    assert pattern_set([])("anything") == []


def test_pattern_set_rejects_an_invalid_pattern_by_name():
    """Same error as config validation, so a caller bypassing the schema still learns why."""
    with pytest.raises(ValueError, match=r"'\(\?=x\)' is not valid RE2"):
        pattern_set(["ok", "(?=x)"])


def test_a_lone_surrogate_does_not_crash_matching():
    """A line RE2 cannot encode is matched as if its lone surrogate were `?`.

    RE2 matches UTF-8 bytes, and a lone surrogate (from a `surrogateescape` decode)
    has no UTF-8 encoding.
    """
    assert sorted(pattern_set(["junk", "x"])("a\udcffx junk")) == [0, 1]
