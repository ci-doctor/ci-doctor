"""`ci-doctor analyze`: a live run by id, or a raw log replayed offline."""

import json
import logging
import os
from enum import StrEnum
from pathlib import Path

import typer

from ci_doctor.cli._context import config_files
from ci_doctor.cli._output import no_color
from ci_doctor.config.loader import load_config
from ci_doctor.core.models import Run
from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import load as load_matchers
from ci_doctor.service.analyze import JobResult, analyze_log, analyze_run
from ci_doctor.service.deliver import payload, post_note, write_artifacts

NAME = "analyze"
log = logging.getLogger("ci_doctor.cli")


class OutputFormat(StrEnum):
    """What `analyze` writes to stdout. The artifacts are written either way."""

    TERMINAL = "terminal"
    JSON = "json"


def run(
    target: str = typer.Argument(
        None, help="Pipeline/run id, or the path to a raw job log to replay offline."
    ),
    job_id: str = typer.Option(None, "--job-id", help="Analyze a single job id."),
    output_format: OutputFormat = typer.Option(
        OutputFormat.TERMINAL,
        "--format",
        help="stdout format. `json` prints the report instead of the rendered panels.",
    ),
) -> None:
    """Explain why a CI run failed.

    Either replays a captured log offline (an existing path) or fetches a live run
    (anything else). Both take the same pipeline: segment, classify, extract
    evidence, report, render. Never changes the run's outcome.

    Args:
        target: A pipeline/run id, or a path to a raw job log.
        job_id: Restrict a live run to one job.
        output_format: What goes to stdout — the rendered panels, or the report
            as JSON for a script or a coding agent to read.
    """
    cfg = load_config(config_files())
    log.debug(f"ci={cfg.ci} scm={cfg.scm_vendor} target={target} job_id={job_id}")
    if target is None:
        raise SetupError("nothing to do: pass a pipeline id, or the path to a log to replay.")
    replay = Path(target).is_file()
    if not replay and _looks_like_a_path(target):
        raise SetupError(f"no such log file: {target}")
    # The last setup step: past it, invariant #3 means nothing changes the exit code.
    matchers = load_matchers(cfg.matchers)

    run = provider = None
    if replay:
        results = analyze_log(Path(target), cfg, matchers)
    else:
        run, provider, results = analyze_run(target, cfg, matchers, job_id)

    if not results:
        typer.echo("no failed jobs to analyze.")
        return
    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(payload(results), indent=2))
    elif cfg.output.terminal:
        _render(results, run)
    write_artifacts(results, cfg, run)
    typer.echo(f"wrote {cfg.output.markdown_path} and {cfg.output.json_path}", err=True)
    if note := post_note(provider, run, results, cfg):
        typer.echo(note, err=True)


def _render(results: list[JobResult], run: Run | None) -> None:
    """The report panels on stdout: a pipeline view for a multi-job live run.

    Args:
        results: One :class:`~ci_doctor.service.analyze.JobResult` per analyzed job.
        run: The analyzed run, or None for offline replay.
    """
    from ci_doctor.render.terminal import render_pipeline, render_terminal

    if run is not None and len(results) > 1:
        render_pipeline(run, results, no_color=no_color())
        return
    wrap = os.environ.get("GITLAB_CI") == "true"  # collapsible only inside GitLab CI
    for r in results:
        render_terminal(r.report, no_color=no_color(), wrap_section=wrap)


def _looks_like_a_path(target: str) -> bool:
    """Whether a missing `analyze` target was meant as a log file, not a run id.

    Args:
        target: The positional argument as typed.

    Returns:
        True for anything carrying a separator or a suffix, so a typo'd path
        reports "no such log file" instead of being fetched as a pipeline id.
    """
    return os.sep in target or "/" in target or bool(Path(target).suffix)
