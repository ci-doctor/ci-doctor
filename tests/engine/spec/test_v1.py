"""The schema 1 spec: both file shapes, the exclusive fields, and errors that name the node."""

import pytest
from pydantic import ValidationError

from ci_doctor.engine import MatcherV1, UnknownSchema, load
from tests.engine.support import v1

DESC = "An output description long enough to pass."


def test_one_output_is_the_matcher_itself():
    """A top-level `match` makes one output whose id is the matcher's."""
    m = v1(match={"regex": "boom"})
    [(oid, where, out)] = m.named_outputs()
    assert (oid, where, out.role) == ("x", "", "tool")


def test_several_outputs_get_dotted_ids():
    """`outputs` keys become `<matcher>.<key>`, in file order."""
    m = v1(
        outputs={
            "check": {"description": DESC, "match": {"regex": "a"}},
            "format": {"description": DESC, "role": "gate", "match": {"regex": "b"}},
        }
    )
    assert [o for o, _, _ in m.named_outputs()] == ["x.check", "x.format"]


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({}, "set `outputs`, or exactly one of `match` or `variants`"),
        (
            {"match": {"regex": "a"}, "variants": {"v": {"match": {"regex": "b"}}}},
            "exactly one of `match` or `variants`",
        ),
        (
            {"match": {"regex": "a"}, "outputs": {"o": {"description": DESC, "match": {"regex": "a"}}}},
            "belong on an output",
        ),
        ({"match": {"regex": "a", "template": "b"}}, "a match node has exactly one of"),
        ({"match": {"nope": "a"}}, "a match node has exactly one of"),
        (
            {"match": {"regex": "a"}, "window": {"before": 1, "from": {"regex": "h", "within": 3}}},
            "`before` and `from`",
        ),
        (
            {"match": {"regex": "a"}, "window": {"after": 1, "until": {"regex": "e"}, "max_lines": 9}},
            "`after` and `until`",
        ),
        (
            {
                "match": {"regex": "a"},
                "window": {"until": {"regex": "e"}, "while": {"regex": "w"}, "max_lines": 9},
            },
            "`until` and `while`",
        ),
        ({"match": {"regex": "a"}, "window": {"until": {"regex": "e"}}}, "needs `max_lines`"),
        ({"match": {"regex": "a"}, "window": {"max_lines": 9}}, "does nothing"),
        ({"match": {"preceded_by": {"regex": "a", "within": 501}}}, "less than or equal to 500"),
        ({"match": {"regex": "a"}, "tags": []}, "at least 1 item"),
        ({"match": {"regex": "a"}, "define": {"lower": "x"}}, "String should match pattern"),
        ({"match": {"regex": "a"}, "classification": ["t"]}, "Extra inputs are not permitted"),
    ],
)
def test_a_malformed_file_is_refused(fields, why):
    """Every exclusive pair and shape rule, with an error naming it."""
    with pytest.raises(ValidationError, match=why):
        v1(**fields)


def test_load_picks_the_model_by_schema():
    """No `schema` is the legacy format; 1 is schema 1; anything else is unknown."""
    legacy = load({"id": "x", "pattern": "a"})
    assert not isinstance(legacy, MatcherV1)
    assert isinstance(
        load({"schema": 1, "id": "x", "description": DESC, "tags": ["t"], "match": {"regex": "a"}}), MatcherV1
    )
    with pytest.raises(UnknownSchema, match="schema 2"):
        load({"schema": 2, "id": "x"})
