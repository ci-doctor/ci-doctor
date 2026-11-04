"""Offline multi-job pipeline e2e.

Two GitLab job logs go through the whole pipeline — segment, classify,
evidence, report — then the non-interactive render. This is the pipeline-level
end-to-end that a single-log replay can't exercise: ordering across jobs, the
code-vs-infra split, and the framing that separates them. No network — the
conftest socket guard holds, and no LLM is configured.
"""

import io

from ci_doctor.config.loader import load_config
from ci_doctor.core.models import Job, Run
from ci_doctor.providers.registry import segmenter_for
from ci_doctor.render.markdown import render_pipeline_markdown
from ci_doctor.render.pipeline import triage
from ci_doctor.render.terminal import render_pipeline
from ci_doctor.service.analyze import _ordered, process_job
from tests import support


def _analyzed_pipeline(registry):
    """A two-job GitLab pipeline, jobs handed in newest-first like the API does.

    Args:
        registry: The matcher registry to load packs from.

    Returns:
        ``(run, results)`` with results in chronological order.
    """
    cfg = load_config(environ={})
    seg = segmenter_for("gitlab")  # these are GitLab logs; don't rely on the default ci
    deploy = Job(
        id="2",
        name="helm-deploy",
        status="failed",
        stage="deploy",
        started_at="2024-01-01T00:10:00Z",
        log=support.gitlab_log(
            [("step_script", ["$ helm upgrade app"])], trailer="ERROR: Job failed: exit code 137"
        ),
    )
    build = Job(
        id="1",
        name="docker-build",
        status="failed",
        stage="build",
        started_at="2024-01-01T00:01:00Z",
        log=support.NOISY["gitlab"],
    )
    jobs = _ordered([deploy, build])  # provider order is newest-first; we re-sort
    results = [process_job(j, cfg, support.registry_matchers(registry), seg) for j in jobs]
    run = Run(id="900", ref="main", sha="abc1234", web_url="https://gl/900", jobs=jobs)
    return run, results


def test_pipeline_orders_and_classifies_offline(fx_registry):
    """Chronological order, and the code-vs-infra split each job earns."""
    _run, results = _analyzed_pipeline(fx_registry)
    assert [r.job.name for r in results] == ["docker-build", "helm-deploy"]  # chronological
    assert triage(results) == (2, 1, 1)  # total, code, infra
    assert results[0].report.is_infra_not_code is False  # script failure is the code's fault
    assert results[1].report.is_infra_not_code is True  # exit 137 is an OOM — infrastructure


def test_pipeline_render_frames_every_job(fx_registry):
    """The non-interactive render headers the run and separates both jobs."""
    run, results = _analyzed_pipeline(fx_registry)
    buf = io.StringIO()
    render_pipeline(run, results, no_color=True, file=buf)
    out = buf.getvalue()
    assert "Pipeline 900" in out
    assert "docker-build (build)" in out and "helm-deploy (deploy)" in out
    md = render_pipeline_markdown(run, results)
    assert "## docker-build" in md and "## helm-deploy" in md
