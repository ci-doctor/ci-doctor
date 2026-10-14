"""Invariants of the workflow files that a reviewer would not spot in a diff."""

import re
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


def test_required_ci_jobs_pass_without_secrets():
    """Fork and Dependabot PRs get no secrets; a step reading one must be skippable, or all-green blocks them."""
    jobs = _load("ci.yml")["jobs"]
    for name in jobs["all-green"]["needs"]:
        for step in jobs[name]["steps"]:
            reads_secret = re.search(r"secrets\.(?!GITHUB_TOKEN\b)", str(step))
            assert not reads_secret or "if" in step, f"{name}: {step.get('name', step.get('uses'))}"


def test_release_puts_optional_docker_after_pypi():
    """Docker Hub is optional: a bad login must fail before the tag, an outage must not block PyPI."""
    steps = [s.get("name") or s.get("uses", "") for s in _load("release.yml")["jobs"]["release"]["steps"]]
    assert steps.index("docker/login-action@v4") < steps.index("Build the Docker image"), steps
    assert steps.index("Publish distributions to PyPI") < steps.index("Push the Docker image"), steps
