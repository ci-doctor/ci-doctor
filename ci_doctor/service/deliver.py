"""Delivering a finished analysis: `report.md`, `report.json` and the MR/PR note."""

import json
from pathlib import Path

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import Run
from ci_doctor.providers.registry import make_scm_provider
from ci_doctor.service.analyze import JobResult


def payload(results: list[JobResult]) -> list[dict]:
    """The reports as JSON-ready dicts, as in `report.json` and `--format json`."""
    return [r.report.model_dump(mode="json") for r in results]


def pipeline_markdown(run: Run | None, results: list[JobResult]) -> str:
    """The Markdown for the artifact and the MR note: a pipeline document for a multi-job run."""
    from ci_doctor.render.markdown import MarkdownRenderer, render_pipeline_markdown

    if run is not None and len(results) > 1:
        return render_pipeline_markdown(run, results)
    return "\n\n---\n\n".join(MarkdownRenderer().render(r.report) for r in results)


def write_artifacts(results: list[JobResult], cfg: Config, run: Run | None) -> None:
    """Write `report.md` and `report.json` where the config says."""
    Path(cfg.output.markdown_path).write_text(pipeline_markdown(run, results))
    Path(cfg.output.json_path).write_text(json.dumps(payload(results), indent=2))


def post_note(ci_provider: object, run: Run | None, results: list[JobResult], cfg: Config) -> str | None:
    """Post the report as an MR/PR note when configured and confidence is medium or better.

    A low-confidence guess on someone's MR is worse than none. Failures are swallowed
    (invariant #3). `ci_provider` lets one vendor's client serve both roles.

    Returns:
        What happened, for the caller to show; None when no note was wanted.
    """
    if not cfg.output.mr_note or run is None or run.mr is None:
        return None
    if not any(r.report.confidence in ("medium", "high") for r in results):
        return "MR note skipped: confidence below medium."
    scm = make_scm_provider(cfg, ci_provider)
    if scm is None:
        return f"MR note skipped: no git-host adapter for {cfg.scm_vendor!r}."
    try:
        scm.post_note(run.mr, pipeline_markdown(run, results), f"<!-- ci-doctor:pipeline:{run.id} -->")
    except Exception as exc:  # noqa: BLE001 - delivery must never break the run
        return f"MR note failed (ignored): {exc}"
    return "posted/updated MR note."
