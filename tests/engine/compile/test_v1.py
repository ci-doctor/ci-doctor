"""Compiling schema 1: templates, defines, the checks, and error paths."""

import pytest

from ci_doctor.engine import CompileError, compile_plan
from ci_doctor.engine.compile.defines import expand_template
from tests.engine.support import v1

DESC = "An output description long enough to pass."


@pytest.mark.parametrize(
    ("template", "regex"),
    [
        ("No space left on device", "No space left on device"),
        ("a.b(c)[d]|e*+?{f}^", r"a\.b\(c\)\[d\]\|e\*\+\?\{f\}\^"),
        ("$FILE:$LINE: error: ...", "(?:[\\w./-]+):(?:\\d+): error: .*"),
        ("costs $$5", r"costs \$5"),
        ("back\\slash", r"back\\slash"),
    ],
)
def test_a_template_is_literal_text_with_placeholders(template, regex):
    """Only `$NAME`, `...` and `$$` mean anything; everything else is escaped."""
    assert expand_template(template, {"FILE": r"[\w./-]+", "LINE": r"\d+"}, "here") == regex


def test_a_regex_inserts_defines_grouped():
    """`%{NAME}` is wrapped, so an alternation in a define can't leak out."""
    plan = compile_plan([v1(define={"ERR": "error|fatal"}, match={"regex": "^%{ERR}:"})])
    assert plan.leaves == ("^(?:error|fatal):",)


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"match": {"template": "$FILE: x"}}, r"x › match: FILE is not defined \(define: none\)"),
        ({"match": {"regex": "%{NOPE}"}}, "NOPE is not defined"),
        ({"match": {"template": "costs $5"}}, "a `\\$` must start `\\$NAME`"),
        ({"match": {"regex": "(?=x)"}}, "not valid RE2"),
        ({"match": {"regex": "a" * 501}}, "over 500"),
        ({"match": {"regex": "x*"}}, "matches an empty line"),
        ({"match": {"not": {"regex": "x"}}}, "matches an empty line"),
        (
            {"match": {"preceded_by": {"preceded_by": {"regex": "a", "within": 1}, "within": 1}}},
            "only regex, template, all, any and not",
        ),
        (
            {
                "match": {"regex": "a"},
                "window": {"until": {"followed_by": {"regex": "e", "within": 2}}, "max_lines": 5},
            },
            r"window\.until: only regex",
        ),
        ({"match": {"all": [{"all": [{"all": [{"all": [{"regex": "a"}]}]}]}]}}, "nests deeper than 4"),
        ({"match": {"any": [{"regex": f"a{i}"} for i in range(32)]}}, "more than 32 nodes"),
    ],
)
def test_a_bad_match_is_a_compile_error_with_a_path(fields, why):
    """Everything the model can't see fails at load, naming where in the file."""
    with pytest.raises(CompileError, match=why):
        compile_plan([v1(**fields)])


def test_the_path_names_output_and_variant():
    """An error deep in a several-output file says exactly where."""
    m = v1(
        outputs={
            "check": {
                "description": DESC,
                "variants": {
                    "full": {
                        "match": {"all": [{"regex": "a"}, {"followed_by": {"template": "$F", "within": 2}}]}
                    }
                },
            }
        }
    )
    with pytest.raises(CompileError) as err:
        compile_plan([m])
    assert err.value.path == "x › outputs.check › variants.full › match.all[1].followed_by"


def test_a_variant_window_overrides_its_outputs():
    """A spelling with its own `window` uses it; the others use the output's."""
    m = v1(
        outputs={
            "o": {
                "description": DESC,
                "window": {"after": 2},
                "variants": {
                    "a": {"match": {"regex": "a"}},
                    "b": {"match": {"regex": "b"}, "window": {"before": 1}},
                },
            }
        }
    )
    [out] = compile_plan([m]).outputs
    assert [(v.name, v.window.after, v.window.before) for v in out.variants] == [("a", 2, 0), ("b", 0, 1)]


def test_leaves_are_shared_and_exclude_drops_outputs():
    """One regex in two matchers is one leaf; an excluded output, and its regexes, are not in the Plan."""
    plan = compile_plan(
        [
            v1(id="a", match={"regex": "boom"}),
            v1(
                id="b",
                outputs={
                    "o": {"description": DESC, "match": {"regex": "boom"}},
                    "p": {"description": DESC, "match": {"regex": "bang"}},
                },
            ),
        ],
        exclude={"b.p"},
    )
    assert plan.leaves == ("boom",)
    assert [o.output.id for o in plan.outputs] == ["a", "b.o"]
