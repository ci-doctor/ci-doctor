"""Linear is checked, not assumed: every feature over a 100,000-line log, under a time budget."""

import time

from ci_doctor.engine import compile_plan, run
from tests.engine.support import v1

DESC = "An output description long enough to pass."


def test_every_feature_on_a_big_log_stays_fast():
    """A quadratic relation or window would take minutes here, not a second."""
    lines = [
        f"line {i} {'error: boom' if i % 97 == 0 else 'ok'}{' HEAD' if i % 50 == 0 else ''}"
        for i in range(100_000)
    ]
    m = v1(
        outputs={
            "near": {
                "description": DESC,
                "match": {
                    "all": [
                        {"regex": "error"},
                        {"preceded_by": {"regex": "HEAD", "within": 400}},
                        {"followed_by": {"regex": "HEAD", "within": 400}},
                    ]
                },
            },
            "block": {
                "description": DESC,
                "match": {
                    "all": [
                        {"regex": "error"},
                        {
                            "not": {
                                "inside": {
                                    "start": {"regex": "HEAD"},
                                    "end": {"regex": "line \\d+5 "},
                                    "max_lines": 500,
                                }
                            }
                        },
                    ]
                },
            },
            "until": {
                "description": DESC,
                "match": {"regex": "error"},
                "window": {"until": {"regex": "HEAD"}, "max_lines": 500},
            },
            "while": {
                "description": DESC,
                "match": {"regex": "HEAD"},
                "window": {"while": {"not": {"regex": "error"}}, "max_lines": 500},
            },
            "from": {
                "description": DESC,
                "match": {"regex": "error"},
                "window": {"from": {"regex": "HEAD", "within": 500}},
            },
        }
    )
    plan = compile_plan([m])
    start = time.perf_counter()
    wins = run(plan, lines)
    assert wins
    assert time.perf_counter() - start < 10  # headroom for a loaded CI runner


def test_a_from_window_over_many_anchors_stays_linear():
    """Each anchor looks its `from` header up in one sorted list, not a fresh sort per anchor."""
    lines = [
        f"# pkg/{i}" if i % 3 == 0 else (f"a.go:{i}: err" if i % 3 == 1 else "x") for i in range(100_000)
    ]
    plan = compile_plan(
        [v1(match={"regex": r"\.go:\d+: err"}, window={"from": {"regex": "^# ", "within": 5}})]
    )
    start = time.perf_counter()
    assert len(run(plan, lines)) == 33_333
    assert time.perf_counter() - start < 10  # was ~13 s when every anchor re-sorted the headers
