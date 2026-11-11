r"""Shared test helpers: synthetic job logs and throwaway matcher registries.

ci-doctor ships no fixture files. A test builds the log it needs, in the real
provider format, with only the shape it checks — so the intent is on the page:

    gh_log([("Run pytest -q", ["E   assert 1 == 2"])])
    gitlab_log([("step_script", ["$ pytest -q", "E   assert 1 == 2"])])

Matcher behaviour against real tool output (does this pack fire on that jest
log?) is the matchers registry's job: it pins every matcher with fixture logs and
`ci-doctor matchers verify`. Here a registry is a few inline matchers written to
`tmp_path` by :func:`write_registry`.

**Adding a provider** means one entry in :data:`LOGS`, a builder for its format;
`test_attribution.py` fails until every registered segmenter has one, and every
provider-parametrized test then covers it.
"""

from collections.abc import Callable
from pathlib import Path

import yaml

from ci_doctor.config.schemas.matchers import MatchersConfig
from ci_doctor.core.models import Section
from ci_doctor.engine import MatcherV1, Spec
from ci_doctor.matchers.index import write_index
from ci_doctor.matchers.registry import load
from ci_doctor.providers.registry import SEGMENTERS, segmenter_for

#: A step or section: its name, then the lines it printed.
Block = tuple[str, list[str]]

_TS = "2026-07-24T20:04:{:02d}.0000000Z "


def gh_log(steps: list[Block], *, exit_code: int | None = 1, cancelled: bool = False) -> str:
    """A GitHub Actions log: every line timestamped, each step a `##[group]` header.

    Args:
        steps: `(group name, output)` per step, e.g. `("Run pytest", [...])`. The
            group wraps only the step's `shell:` header, its output follows
            `##[endgroup]` — the shape the runner really prints. Output lines go in
            verbatim, so a `##[group]` line there is a sub-group of the step.
        exit_code: The runner's `##[error]Process completed with exit code N.`
            after the last step; None for a step that ended with no exit code.
            Either way the job's own `Post job cleanup.` closes it.
        cancelled: The job was cancelled or timed out mid-step: the log ends on
            `##[error]The operation was canceled.` and the last step stays open.

    Returns:
        The raw log, newline-terminated.
    """
    lines = ["Current runner version: '2.336.0'"]
    for name, output in steps:
        lines += [f"##[group]{name}", "shell: /usr/bin/bash -e {0}", "##[endgroup]", *output]
    if cancelled:
        lines.append("##[error]The operation was canceled.")
    else:
        if exit_code is not None:
            lines.append(f"##[error]Process completed with exit code {exit_code}.")
        lines += ["Post job cleanup.", "Cleaning up orphan processes"]
    return "".join(_TS.format(i % 60) + line + "\n" for i, line in enumerate(lines))


def gitlab_log(
    sections: list[Block], *, trailer: str | None = "ERROR: Job failed: exit code 1", open_last: bool = False
) -> str:
    r"""A GitLab job trace: ANSI-wrapped `section_start`/`section_end` markers.

    Args:
        sections: `(section name, output)` per section, e.g. `("step_script", [...])`.
        trailer: The runner's closing line, after every section.
        open_last: Leave the last section without its `section_end`, as a job
            killed inside it (a timeout) does.

    Returns:
        The raw trace, newline-terminated.
    """
    out = ["Running with gitlab-runner 16.11.0 (a1b2c3d4)"]
    for i, (name, lines) in enumerate(sections):
        out.append(f"\x1b[0Ksection_start:{1735689600 + 10 * i}:{name}\r\x1b[0K")
        out += lines
        if not (open_last and i == len(sections) - 1):
            out.append(f"\x1b[0Ksection_end:{1735689609 + 10 * i}:{name}\r\x1b[0K")
    if trailer is not None:
        out.append(trailer)
    return "\n".join(out) + "\n"


def _gh_script(lines: list[str], exit_code: int) -> str:
    return gh_log([("Run ./ci.sh", lines)], exit_code=exit_code)


def _gitlab_script(lines: list[str], exit_code: int) -> str:
    return gitlab_log([("step_script", lines)], trailer=f"ERROR: Job failed: exit code {exit_code}")


#: Provider name -> a builder for one failed script step in its format.
LOGS: dict[str, Callable[[list[str], int], str]] = {"github": _gh_script, "gitlab": _gitlab_script}
#: Every provider with a log format, for `pytest.mark.parametrize`.
PROVIDERS = sorted(LOGS)


def script_log(provider: str, lines: list[str], exit_code: int = 1) -> str:
    """One script step printing `lines` and failing, in `provider`'s format.

    Args:
        provider: A key of :data:`LOGS`.
        lines: What the script printed.
        exit_code: The exit code the runner reports.

    Returns:
        The raw log.
    """
    return LOGS[provider](lines, exit_code)


#: The pytest failure most cases print, so only the runner framing differs.
FAIL = [
    "$ pytest -q",
    "=== FAILURES ===",
    "E   assert 1 == 2",
    "=== short test summary info ===",
    "FAILED tests/test_math.py::test_add - assert 1 == 2",
]
GH_CACHE = ("Run actions/cache@v4", ["##[warning]Failed to restore cache: ERROR 404: entry not found"])
GL_CACHE = ("restore_cache", ["WARNING: Failed to extract cache"])
#: The "blamed the cache" regression: loud fetch warnings, then the script fails.
NOISY = {
    "github": gh_log([GH_CACHE, ("Run pytest -q", FAIL)]),
    "gitlab": gitlab_log([GL_CACHE, ("step_script", FAIL)]),
}


def segment(provider: str, raw_log: str) -> list[Section]:
    """Parse a raw log with the segmenter for that provider's format.

    Args:
        provider: Provider name.
        raw_log: The raw log text.

    Returns:
        The section tree.

    Raises:
        KeyError: If the provider has no registered segmenter.
    """
    if provider not in SEGMENTERS:
        raise KeyError(f"no segmenter registered for {provider!r}")
    return segmenter_for(provider).segment(raw_log)


# --- matcher registries ------------------------------------------------------------

#: Two matchers in the shape real ones take: a block closed by `until`, and an anchor with lines after it.
PY_MATCHERS = {
    "pytest": {
        "schema": 1,
        "role": "tool",
        "description": "pytest's === FAILURES === block, from the first failing test through to the short summary.",
        "tags": ["python", "test"],
        "match": {"regex": "^=+ FAILURES =+"},
        "window": {"until": {"regex": "^=+ short test summary"}, "max_lines": 500},
    },
    "python_traceback": {
        "schema": 1,
        "role": "tool",
        "description": "An uncaught Python exception. The exception type and message are on the traceback's last line.",
        "tags": ["python", "runtime"],
        "match": {"regex": r"^\s*Traceback \(most recent call last\):"},
        "window": {"after": 12},
    },
}
#: `all` -> `python` -> both matchers, so bundle nesting is exercised too.
PY_BUNDLES = {"python": ["pytest", "python_traceback"], "all": ["python"]}


def write_registry(
    root: Path,
    matchers: dict[str, dict],
    bundles: dict[str, list[str]] | None = None,
    fixtures: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Write a small matcher registry and index it with ci-doctor's own code.

    Args:
        root: Where to write it; created if missing.
        matchers: `{name: matcher fields}`, without `id`. The first tag picks
            the ecosystem directory.
        bundles: `{name: [included names]}`.
        fixtures: `{matcher name: {stem: log text}}`, written as
            `fixtures/<stem>.log` next to the matcher for `matchers verify`. No
            `.expected` files: run `verify(root, update=True)` to bless them.

    Returns:
        `root`, holding `matchers/<eco>/<name>/matcher.yaml`, `bundles/<name>.yaml`
        and a current `index.yaml`.
    """
    for name, fields in matchers.items():
        path = root / "matchers" / fields["tags"][0] / name / "matcher.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(_dump({"id": name, **fields}))
        for stem, log in (fixtures or {}).get(name, {}).items():
            (path.parent / "fixtures").mkdir(exist_ok=True)
            (path.parent / "fixtures" / f"{stem}.log").write_text(log)
    (root / "bundles").mkdir(parents=True, exist_ok=True)
    for name, include in (bundles or {}).items():
        body = {
            "id": name,
            "description": f"The {name} bundle, written by the test helpers.",
            "include": include,
        }
        (root / "bundles" / f"{name}.yaml").write_text(_dump(body))
    write_index(root)
    return root


def _dump(doc: dict) -> str:
    """YAML as a person writes it: key order kept, tag lists inline, no wrapping."""
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=None, width=4096)


def py_registry(root: Path) -> Path:
    """The two-matcher Python registry (:data:`PY_MATCHERS`, :data:`PY_BUNDLES`)."""
    return write_registry(root, PY_MATCHERS, PY_BUNDLES)


def registry_matchers(root: Path, *refs: str) -> list[Spec]:
    """Load matchers from a registry, as `analyze` would from a real one.

    Args:
        root: A registry from :func:`write_registry`.
        *refs: Names in it (`pytest`, `python`); defaults to the `all` bundle.

    Returns:
        The matchers, ids as `fx/<name>`.
    """
    include = [f"fx/{r}" for r in refs or ("all",)]
    return load(MatchersConfig(registries={"fx": str(root)}, include=include))


#: Two of the formerly shipped matchers, for tests about wrappers vs tools.
TSC = MatcherV1.model_validate(
    {
        "schema": 1,
        "id": "t/tsc",
        "description": "A TypeScript compile error (`error TS2345:`) with the source excerpt around it.",
        "tags": ["js", "build"],
        "match": {"regex": r"error TS\d+:"},
        "window": {"before": 1, "after": 3},
    }
)
NPM = MatcherV1.model_validate(
    {
        "schema": 1,
        "id": "t/npm",
        "role": "wrapper",
        "description": "`npm ERR!` lines. A wrapper: npm reports the failure of the script it ran, never the cause.",
        "tags": ["js", "deps"],
        "match": {"regex": "npm ERR!"},
        "window": {"before": 2, "after": 10},
    }
)
