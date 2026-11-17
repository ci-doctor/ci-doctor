"""Attribution: which phase a job failed in, and which rule decided it.

Each case builds the smallest log that has the shape the rule keys on, in every
provider's format: the verdict lives in provider-neutral `core/`, so the same
scenario must classify identically whoever printed the log. No network, no LLM.
"""

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.core.attribution import attribute
from ci_doctor.core.models import FailureReason, Job, LogLine, Phase, Section
from ci_doctor.core.phases import assign_phases
from tests import support
from tests.support import FAIL, GH_CACHE, GL_CACHE, NOISY, gh_log, gitlab_log

U, R = FailureReason.UNKNOWN, FailureReason


def _classify(provider, log, reason=U):
    """Segment, phase-map and classify a raw log the way `analyze` does."""
    job = Job(id="1", name="j", status="failed", failure_reason=reason, log=log or None)
    job.sections = support.segment(provider, log)
    assign_phases(job.sections, load_config(environ={}).phases)
    return attribute(job, job.sections)


# (id, provider, log, provider reason) -> (phase, reason, rule_id)
CASES = [
    # The runner's exit code is the verdict when the provider gave none.
    (
        "exit_code",
        "github",
        gh_log([("Run pytest -q", FAIL)]),
        U,
        ("script", "script_failure", "trailer_exit_code"),
    ),
    (
        "exit_code",
        "gitlab",
        gitlab_log([("step_script", FAIL)]),
        U,
        ("script", "script_failure", "trailer_exit_code"),
    ),
    # 137 is SIGKILL, the OOM killer: infrastructure, not the script.
    (
        "oom_137",
        "github",
        gh_log([("Run ./build_big.sh", ["Compiling everything at once..."])], exit_code=137),
        U,
        ("script", "runner_system", "oom_137"),
    ),
    (
        "oom_137",
        "gitlab",
        gitlab_log([("step_script", ["$ ./build_big.sh"])], trailer="ERROR: Job failed: exit code 137"),
        U,
        ("script", "runner_system", "oom_137"),
    ),
    # A timeout blames whatever was still running when the runner pulled the plug.
    (
        "timeout",
        "github",
        gh_log([("Run ./soak.sh", ["still working..."])], cancelled=True),
        R.TIMEOUT,
        ("script", "timeout", "reason_timeout_open_section"),
    ),
    (
        "timeout",
        "gitlab",
        gitlab_log(
            [("step_script", ["still working..."])],
            open_last=True,
            trailer="ERROR: Job failed: execution took longer than 1h0m0s",
        ),
        R.TIMEOUT,
        ("script", "timeout", "reason_timeout_open_section"),
    ),
    # No provider reason, but a step never finished: execution died inside it.
    (
        "unclosed",
        "github",
        gh_log([("Run ./soak.sh", ["still working..."])], cancelled=True),
        U,
        ("script", "script_failure", "unclosed_section"),
    ),
    (
        "unclosed",
        "gitlab",
        gitlab_log([("step_script", ["still working..."])], open_last=True, trailer=None),
        U,
        ("script", "script_failure", "unclosed_section"),
    ),
    # The provider's reason is authoritative for everything but script_failure.
    (
        "missing_dep",
        "github",
        gh_log([("Run actions/download-artifact@v4", ["##[error]Unable to download artifact(s)"])]),
        R.MISSING_DEPENDENCY,
        ("fetch", "missing_dependency", "reason_missing_dependency"),
    ),
    (
        "missing_dep",
        "gitlab",
        gitlab_log([("download_artifacts", ["ERROR: Downloading artifacts failed"])]),
        R.MISSING_DEPENDENCY,
        ("fetch", "missing_dependency", "reason_missing_dependency"),
    ),
    (
        "runner_system",
        "github",
        gh_log([("Initialize containers", ["Error response from daemon: pull failed"])]),
        R.RUNNER_SYSTEM,
        ("prepare", "runner_system", "reason_runner_system"),
    ),
    (
        "runner_system",
        "gitlab",
        gitlab_log(
            [("prepare_executor", ["Pulling docker image build:latest ..."])],
            trailer="ERROR: Job failed (system failure): prepare environment: exit status 1",
        ),
        R.RUNNER_SYSTEM,
        ("prepare", "runner_system", "reason_runner_system"),
    ),
    # GitLab's own system-failure trailer settles it even with no provider reason.
    (
        "system_trailer",
        "gitlab",
        gitlab_log(
            [("prepare_executor", ["Pulling docker image build:latest ..."])],
            trailer="ERROR: Job failed (system failure): prepare environment: exit status 1",
        ),
        U,
        ("prepare", "runner_system", "trailer_system_failure"),
    ),
    # The "blamed the cache" regression: script_failure is SCRIPT, whatever fetch said.
    *[
        (
            "noisy_script_failure",
            p,
            NOISY[p],
            R.SCRIPT_FAILURE,
            ("script", "script_failure", "script_failure_is_script"),
        )
        for p in NOISY
    ],
    # No exit code anywhere: the last section with a real error, never a warning-only one.
    (
        "warning_only_fetch",
        "github",
        gh_log([GH_CACHE, ("Run acme/custom-tool@v2", ["ERROR: custom tool stopped"])], exit_code=None),
        U,
        ("script", "script_failure", "last_error_section"),
    ),
    (
        "warning_only_fetch",
        "gitlab",
        gitlab_log([GL_CACHE, ("step_script", ["ERROR: custom tool stopped"])], trailer="ERROR: Job failed"),
        U,
        ("script", "script_failure", "last_error_section"),
    ),
    # No log at all: the job never reached a runner.
    ("empty_log", "github", "", R.TIMEOUT, ("provision", "no_runner", "empty_log_no_runner")),
    ("empty_log", "gitlab", "", R.TIMEOUT, ("provision", "no_runner", "empty_log_no_runner")),
]


@pytest.mark.parametrize(
    ("provider", "log", "reason", "expected"), [c[1:] for c in CASES], ids=[f"{c[1]}-{c[0]}" for c in CASES]
)
def test_attribution(provider, log, reason, expected):
    """A log shape classifies to its phase, reason and rule, in every provider's format."""
    attr = _classify(provider, log, reason)
    assert (attr.phase, attr.reason, attr.rule_id) == expected


def test_a_log_that_could_not_be_read_is_not_a_job_that_never_ran():
    """The provider's reason still names the phase, but without the log nothing confirms it."""
    never_ran = Job(id="1", name="j", status="failed", failure_reason=R.SCRIPT_FAILURE, log=None)
    assert attribute(never_ran, []).rule_id == "empty_log_no_runner"

    unreadable = Job(id="1", name="j", status="failed", failure_reason=R.SCRIPT_FAILURE, log_error="HTTP 403")
    verdict = attribute(unreadable, [])
    assert (verdict.phase, verdict.reason, verdict.rule_id) == (
        Phase.SCRIPT,
        R.SCRIPT_FAILURE,
        "script_failure_is_script",
    )
    assert verdict.confidence == "low"


def test_an_unreadable_log_with_no_provider_reason_is_no_signal():
    """Nothing to go on is said as such, not guessed as a runner that never came."""
    job = Job(id="1", name="j", status="failed", failure_reason=U, log_error="HTTP 403")
    verdict = attribute(job, [])
    assert (verdict.phase, verdict.rule_id, verdict.confidence) == (Phase.UNKNOWN, "no_signal", "low")


def test_every_scenario_is_covered_by_every_provider():
    """A scenario proved against one log format only is an untested format.

    Runner-specific trailers (GitLab's `system failure`) are the one exception.
    """
    only = {"system_trailer"}
    by_case = {}
    for case, provider, *_ in CASES:
        by_case.setdefault(case, set()).add(provider)
    gaps = {c: set(support.PROVIDERS) - p for c, p in by_case.items() if c not in only}
    assert not any(gaps.values()), f"missing providers: {gaps}"


@pytest.mark.parametrize("provider", support.PROVIDERS)
def test_noisy_fetch_warnings_are_context_not_blame(provider):
    """The warnings that used to get the cache blamed surface as a contributing phase."""
    attr = _classify(provider, NOISY[provider], R.SCRIPT_FAILURE)
    assert attr.phase == Phase.SCRIPT
    assert attr.secondary_phases == [Phase.FETCH]


def test_oom_evidence_is_the_exit_code_line():
    """The verdict quotes the runner line that proved it."""
    attr = _classify(
        "gitlab", gitlab_log([("step_script", ["$ make"])], trailer="ERROR: Job failed: exit code 137")
    )
    assert attr.terminal_evidence == "ERROR: Job failed: exit code 137"


def _section(name, phase, *texts, closed=True):
    """A ready-to-classify section, so these cases need no log at all."""
    sec = Section(name=name, closed=closed, lines=[LogLine(number=i, text=t) for i, t in enumerate(texts, 1)])
    sec.phase = phase
    return sec


# The runner's own two ways of saying "warning": GitLab's literal prefix, and
# GitHub's annotation. The log cases above cannot pin this: in them the noisy
# section comes *before* the real error, so log order alone would keep it from
# being blamed.
@pytest.mark.parametrize(
    "warning",
    [
        "WARNING: failed to extract cache: ERROR 404 Not Found",
        "##[warning]Failed to restore cache: ERROR 404 Not Found",
    ],
)
def test_a_runner_warning_is_never_the_verdict(warning):
    """The "blamed the cache" bug: a warning that *mentions* an error is not one.

    The warning section is deliberately last, so only the rule — not log order —
    can keep it from being blamed.
    """
    sections = [
        _section("step_script", Phase.SCRIPT, "$ ./deploy.sh", "ERROR: deploy script aborted"),
        _section("archive_cache", Phase.POST, warning),
    ]
    job = Job(id="1", name="j", status="failed", failure_reason=FailureReason.UNKNOWN, log="x")

    attr = attribute(job, sections)
    assert attr.rule_id == "last_error_section"
    assert attr.phase == Phase.SCRIPT, f"{warning!r} was treated as fatal"
    assert attr.secondary_phases == [Phase.POST]  # mentioned as context, never blamed


# The other half of the same rule, and the reason it is case-sensitive: apt, pip
# and docker all print "Warning:" from inside a step. That is the step's output,
# not the runner excusing it, so it must not buy the section an exemption.
@pytest.mark.parametrize(
    "line",
    [
        "Warning: apt does not have a stable CLI interface. ERROR downloading package",
        "warning: docker build ran with an ERROR in the final layer",
    ],
)
def test_a_tools_own_warning_is_not_a_runner_advisory(line):
    """A step printing the word "warning" is still a step that failed."""
    sections = [
        _section("get_sources", Phase.FETCH, "Syncing repository"),
        _section("step_script", Phase.SCRIPT, "$ ./build.sh", line),
    ]
    job = Job(id="1", name="j", status="failed", failure_reason=FailureReason.UNKNOWN, log="x")

    attr = attribute(job, sections)
    assert attr.phase == Phase.SCRIPT, f"{line!r} was excused as a runner advisory"
    assert attr.rule_id == "last_error_section"


# The mirror of the rule above: the runner's own error annotation. Easy to miss,
# because the common one ("Process completed with exit code 1") is also caught by
# the exit-code branch — so only an annotation *without* an exit code proves it.
@pytest.mark.parametrize(
    "error",
    [
        "##[error]The operation was canceled.",
        "##[error]Unable to download artifact(s): Artifact not found for name: compile",
    ],
)
def test_a_runner_error_annotation_is_blamed(error):
    """A runner saying "this failed" is fatal even with no exit code in the text."""
    sections = [
        _section("get_sources", Phase.FETCH, "Syncing repository"),
        _section("step_script", Phase.SCRIPT, "building", error),
    ]
    job = Job(id="1", name="j", status="failed", failure_reason=FailureReason.UNKNOWN, log="x")

    attr = attribute(job, sections)
    assert attr.rule_id == "last_error_section"
    assert attr.phase == Phase.SCRIPT, f"{error!r} was not recognised as a failure"


def test_the_classifier_does_not_carry_a_second_matcher_catalogue():
    """`_ERROR_RE` decides which *section* failed; `extraction.matchers` picks lines.

    Keeping vendor signatures out of the classifier is what stops the catalogue
    being duplicated in code, where no `.ci-doctor.yml` could tune it. A tool that
    fails at all makes its runner say so, so the coarse question needs no help.
    """
    from ci_doctor.core.attribution import _is_error_line

    assert not _is_error_line("npm ERR! code ELIFECYCLE")
    assert not _is_error_line("    at Object.<anonymous> (/app/index.js:12:9)")
    assert _is_error_line("ERROR: Job failed: exit code 1")


def test_every_segmenter_has_a_log_builder():
    """Adding a provider means teaching the tests its log format.

    Without a builder in `support.LOGS`, every provider-parametrized test would
    silently skip the new format instead of failing.
    """
    assert set(support.SEGMENTERS) == set(support.LOGS)
