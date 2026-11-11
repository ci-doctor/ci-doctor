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
