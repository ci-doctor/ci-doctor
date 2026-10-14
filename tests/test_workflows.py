"""Invariants of the workflow files that a reviewer would not spot in a diff."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github/workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_all_green_needs_every_other_ci_job():
    """The ruleset requires only `all-green`; a job it does not need is required by nothing."""
    jobs = _load("ci.yml")["jobs"]
    assert set(jobs["all-green"]["needs"]) == set(jobs) - {"all-green"}


def test_release_pushes_the_tag_only_after_everything_built():
    """A build error after the tag push leaves a tag with no release; the first alpha failed so."""
    steps = [s.get("name", "") for s in _load("release.yml")["jobs"]["release"]["steps"]]
    push = steps.index("Commit and tag")
    built = [
        "Build the distributions",
        "Build the Docker image",
        "Generate the config JSON Schema",
        "Generate the SBOM",
    ]
    assert all(steps.index(name) < push for name in built), steps
