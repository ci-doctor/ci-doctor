"""Golden-fixture tests for a registry's matchers, run with ci-doctor's own engine.

Each fixture is a job log plus one `.expected` file per output, holding exactly what
that output alone selects from it; an empty `.expected` means it must not fire.
`--update` writes them and the reviewer reads them in the PR diff, so "what this
matcher catches" is reviewed as plain text. No LLM is involved.

A fixture declares its framing by its name (`matchers/logs.py`), so a `^` anchor sees
what it sees in production.

Every check here is about correctness, so it applies to any registry. How many
fixtures are enough is a registry's policy, passed in as a `Policy`.
"""

import difflib
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ci_doctor.engine import Plan, Spec, Window, compile_plan, run, select
from ci_doctor.errors import SetupError
from ci_doctor.matchers.logs import read_fixture
from ci_doctor.matchers.validate import load_matcher, matcher_files

MAX_FIXTURE_BYTES = 200 * 1024

#: The lines a runner prints when *any* step fails. A non-fallback output that fires on
#: one of them fires on every failed job, whatever tool ran (GUIDELINES §7).
RUNNER_TRAILERS = (
    "##[error]Process completed with exit code 1.",
    "Error: Process completed with exit code 1.",
    "Process completed with exit code 1.",
    "ERROR: Job failed: exit code 1",
    "ERROR: Job failed: command terminated with exit code 1",
    "Cleaning up project directory and file based variables",
)


@dataclass(frozen=True)
class Policy:
    """How much a registry asks of each output: its policy, not ci-doctor's.

    Attributes:
        min_positive: Fixtures each output must fire on.
        min_negative: Fixtures each output must stay silent on.
        exempt: A file in the registry naming matchers held to one positive fixture
            instead; it may only shrink.
        cross_fire: Fail when an output fires on another matcher's fixture.
    """

    min_positive: int = 3
    min_negative: int = 2
    exempt: str = "verify-exempt.txt"
    cross_fire: bool = False


def selection(lines: list[str], windows: list[Window]) -> str:
    """What these windows select, in `.expected` form.

    `select` falls back to the whole log when nothing matched, which would make a
    negative fixture compare against the log itself; no window means empty here.

    Args:
        lines: The fixture, from :func:`read_fixture`.
        windows: One output's windows on it.

    Returns:
        The selected lines with elision markers, newline-terminated, or "" when
        nothing anchored.
    """
    if not windows:
        return ""
    return "\n".join(select(lines, windows, tail_lines=0).lines) + "\n"


def golden(log: Path, output_id: str, matcher_id: str) -> Path:
    """The `.expected` file for one output on one fixture.

    Args:
        log: The fixture.
        output_id: The output's id.
        matcher_id: Its matcher's id.

    Returns:
        `<case>.expected` for a one-output matcher, `<case>.<output>.expected` otherwise.
    """
    stem = log.name.removesuffix(".log")
    suffix = "" if output_id == matcher_id else "." + output_id.removeprefix(matcher_id + ".")
    return log.with_name(f"{stem}{suffix}.expected")


@dataclass
class OutputReport:
    """What `verify` found for one output."""

    positive: int = 0
    negative: int = 0
    variants: dict[str, bool] = field(default_factory=dict)


@dataclass
class Report:
    """The whole run: per output, cross-fires, and every problem. `--json` writes it."""

    outputs: dict[str, OutputReport] = field(default_factory=dict)
    cross_fire: list[dict[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """The report as JSON, for a registry's own guards to read.

        Returns:
            Indented JSON.
        """
        doc = {
            "outputs": {k: vars(v) for k, v in self.outputs.items()},
            "cross_fire": self.cross_fire,
            "problems": self.problems,
        }
        return json.dumps(doc, indent=2) + "\n"


@dataclass
class _Matcher:
    """One matcher under test: its spec, Plan, fixtures and their lines."""

    path: Path
    spec: Spec
    plan: Plan
    logs: dict[Path, list[str]]


def _exempt(root: Path, policy: Policy) -> set[str]:
    """Names held to one positive fixture; blank lines and `#` comments skipped.

    Args:
        root: The registry directory.
        policy: Names the exemption file.

    Returns:
        The listed names.
    """
    path = root / policy.exempt
    if not path.is_file():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def _fixtures(root: Path, path: Path, report: Report) -> dict[Path, list[str]]:
    """A matcher's fixture logs, read; an oversized one is a problem, not a fixture.

    Args:
        root: The registry directory, for readable paths.
        path: The `matcher.yaml`.
        report: Collects problems.

    Returns:
        Each fixture's lines, by path.
    """
    logs = {}
    for log in sorted((path.parent / "fixtures").glob("*.log")):
        if (size := log.stat().st_size) > MAX_FIXTURE_BYTES:
            report.problems.append(f"{log.relative_to(root)}: {size} bytes, over {MAX_FIXTURE_BYTES}")
            continue
        logs[log] = read_fixture(log)
    return logs


def _goldens(root: Path, m: _Matcher, report: Report, *, update: bool) -> None:
    """Compare (or write) each output's `.expected` on each fixture, and count them.

    Each fixture runs once; outputs don't affect each other, so filtering its windows
    by output is the same as running each output alone.

    Args:
        root: The registry directory, for readable paths.
        m: The matcher under test.
        report: Collects counts, variant coverage and problems.
        update: Write the `.expected` files instead of comparing.
    """
    stats = [
        report.outputs.setdefault(
            o.output.id, OutputReport(variants=dict.fromkeys((v.name for v in o.variants if v.name), False))
        )
        for o in m.plan.outputs
    ]
    for log, lines in m.logs.items():
        windows = run(m.plan, lines)
        for o, out in enumerate(m.plan.outputs):
            own = [w for w in windows if w.output == o]
            stats[o].variants |= {w.variant: True for w in own if w.variant}
            path = golden(log, out.output.id, m.spec.id)
            if problem := _compare(path, selection(lines, own), out.output.id, stats[o], update=update):
                report.problems.append(f"{log.relative_to(root)}: {problem}")
    report.problems += [
        f"{out.output.id} ({name}): this variant anchors in none of its matcher's fixtures"
        for out, st in zip(m.plan.outputs, stats, strict=True)
        for name, covered in st.variants.items()
        if not covered
    ]


def _compare(path: Path, got: str, output_id: str, stats: OutputReport, *, update: bool) -> str | None:
    """Write or compare one golden, counting it as positive or negative.

    Args:
        path: The `.expected` file.
        got: What the output selects now.
        output_id: Names it in a diff.
        stats: Its output's counts.
        update: Write instead of comparing.

    Returns:
        What is wrong, or None.
    """
    if update:
        path.write_text(got, encoding="utf-8")
    elif not path.is_file():
        return f"no {path.name}; run `ci-doctor matchers verify --update`"
    want = path.read_text(encoding="utf-8")
    if want:
        stats.positive += 1
    else:
        stats.negative += 1
    if got == want:
        return None
    diff = difflib.unified_diff(want.splitlines(), got.splitlines(), path.name, "actual", lineterm="")
    return f"{output_id} differs from {path.name}\n" + "\n".join(diff)


def _counts(output_id: str, stats: OutputReport, policy: Policy, *, exempt: bool) -> Iterator[str]:
    """The fixture-count rule for one output.

    Args:
        output_id: For messages.
        stats: Its counts.
        policy: The registry's bar.
        exempt: Whether its matcher is on the exemption list.

    Yields:
        One message when the counts break the rule.
    """
    low, high = policy.min_positive, policy.min_negative
    if exempt:
        if stats.positive >= low and stats.negative >= high:
            yield f"{output_id}: meets {low} + {high} fixtures, remove it from {policy.exempt}"
        elif stats.positive < 1:
            yield f"{output_id}: needs at least 1 positive fixture (exempt from the full minimum)"
    elif stats.positive < low or stats.negative < high:
        yield (
            f"{output_id}: has {stats.positive} positive and {stats.negative} negative fixtures, "
            f"needs at least {low} and {high}"
        )


def _digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _cross_fire(root: Path, matchers: list[_Matcher], report: Report) -> None:
    """Outputs firing on another matcher's fixture, run as one Plan: one pass per fixture.

    A fallback is a catch-all by design, so it's not reported. A fixture whose content
    is also one of the other matcher's own fixtures is shared on purpose.

    Args:
        root: The registry directory, for readable paths.
        matchers: Every matcher under test.
        report: Collects the cross-fires.
    """
    plan = compile_plan([m.spec for m in matchers])
    owner = {o.output.id: m for m in matchers for o in m.plan.outputs}
    digests = {log: _digest(lines) for m in matchers for log, lines in m.logs.items()}
    own = {id(m): {digests[log] for log in m.logs} for m in matchers}
    for m in matchers:
        for log, lines in m.logs.items():
            fired = {plan.outputs[w.output].output for w in run(plan, lines)}
            for out in sorted(fired, key=lambda o: o.id):
                other = owner[out.id]
                if other is m or out.role == "fallback" or digests[log] in own[id(other)]:
                    continue
                report.cross_fire.append({"output": out.id, "fixture": str(log.relative_to(root))})


def _runner_trailers(matchers: list[_Matcher], report: Report) -> None:
    """Outputs that fire on a runner's own "job failed" line, and so on every failed job.

    Args:
        matchers: Every matcher under test.
        report: Collects problems.
    """
    for m in matchers:
        for w in run(m.plan, list(RUNNER_TRAILERS)):
            out = m.plan.outputs[w.output].output
            if out.role != "fallback":
                line = RUNNER_TRAILERS[w.anchor]
                report.problems.append(
                    f"{out.id}: fires on the runner's own line {line!r}, so on every failed job"
                )


def check(root: Path, *, update: bool = False, policy: Policy | None = None) -> tuple[int, Report]:
    """Run every check over a registry and report, without raising on a failed check.

    Args:
        root: The registry directory.
        update: Write each output's `.expected` from the current output instead of
            comparing. The fixture counts are still enforced.
        policy: The registry's bar; the defaults are the official registry's.

    Returns:
        How many matchers were checked, and the report.

    Raises:
        SetupError: If `root` holds no matchers.
    """
    policy = policy or Policy()
    paths = matcher_files(root)
    report = Report()
    exempt = _exempt(root, policy)
    report.problems += [
        f"{policy.exempt}: {name!r} is not a matcher in this registry"
        for name in sorted(exempt - {p.parent.name for p in paths})
    ]
    matchers = []
    for path in paths:
        try:
            spec = load_matcher(path, author=True)
        except SetupError as err:
            report.problems.append(str(err))
            continue
        m = _Matcher(path, spec, compile_plan([spec]), _fixtures(root, path, report))
        matchers.append(m)
        _goldens(root, m, report, update=update)
        for out in m.plan.outputs:
            stats = report.outputs[out.output.id]
            report.problems += _counts(out.output.id, stats, policy, exempt=spec.id in exempt)
    _cross_fire(root, matchers, report)
    if policy.cross_fire:
        report.problems += [
            f"{c['output']} fires on {c['fixture']}, another matcher's fixture" for c in report.cross_fire
        ]
    _runner_trailers(matchers, report)
    return len(paths), report


def verify(
    root: Path, *, update: bool = False, policy: Policy | None = None, json_out: Path | None = None
) -> int:
    """Run the golden-fixture checks over a whole registry.

    Args:
        root: The registry directory.
        update: Write each output's `.expected` instead of comparing.
        policy: The registry's bar; the defaults are the official registry's.
        json_out: Also write the full report here, pass or fail.

    Returns:
        The number of matchers checked.

    Raises:
        SetupError: Listing every failure.
    """
    n, report = check(root, update=update, policy=policy)
    if json_out is not None:
        json_out.write_text(report.to_json(), encoding="utf-8")
    if report.problems:
        raise SetupError(f"verify failed ({len(report.problems)}):\n" + "\n".join(report.problems))
    return n
