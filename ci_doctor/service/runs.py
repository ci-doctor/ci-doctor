"""Listing a project's CI runs, behind the MCP `list_runs` tool."""

from dataclasses import replace
from datetime import date, datetime

from ci_doctor.config.schemas import Config
from ci_doctor.core.models import RunFilter, RunStatus, RunSummary
from ci_doctor.core.redact import redact_text
from ci_doctor.errors import SetupError
from ci_doctor.providers.api_client import ApiError
from ci_doctor.providers.registry import make_ci_provider

#: The most runs one listing returns: enough to pick from, small enough to read.
MAX_LIMIT = 50


def list_runs(cfg: Config, run_filter: RunFilter) -> list[RunSummary]:
    """The project's runs that match, newest first, their titles redacted (invariant #5).

    Args:
        cfg: The effective config; `ci:` names the CI system.
        run_filter: What to keep.

    Returns:
        At most `run_filter.limit` runs; empty when none match.

    Raises:
        SetupError: On a bad filter, an unknown project, a refused token or an unreachable API.
    """
    _check(run_filter)
    try:
        runs = make_ci_provider(cfg).list_runs(run_filter)
    except (ApiError, ValueError) as err:
        raise SetupError(f"could not list runs: {err}") from None
    return [replace(r, title=redact_text(r.title, cfg.redaction)) if r.title else r for r in runs]


def _check(run_filter: RunFilter) -> None:
    """Refuse a filter the CI system would misread rather than answer wrongly.

    Raises:
        SetupError: If the limit is out of range, a date is not ISO 8601, or the status is `unknown`.
    """
    if not 1 <= run_filter.limit <= MAX_LIMIT:
        raise SetupError(f"limit must be between 1 and {MAX_LIMIT}, got {run_filter.limit}")
    if run_filter.status == RunStatus.UNKNOWN:
        raise SetupError("status `unknown` only describes a run; filter by a known status")
    for name, value in (("since", run_filter.since), ("until", run_filter.until)):
        if value is not None and not _is_iso(value):
            raise SetupError(f"{name} must be an ISO 8601 date or time, e.g. 2026-11-01, got {value!r}")


def _is_iso(value: str) -> bool:
    """Whether `value` is an ISO 8601 date (`2026-11-01`) or date and time."""
    for parse in (date.fromisoformat, datetime.fromisoformat):
        try:
            parse(value)
        except ValueError:
            continue
        return True
    return False
