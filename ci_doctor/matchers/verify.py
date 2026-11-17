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
#: The most diff lines shown for one mismatch; the cut is marked (invariant #6).
MAX_DIFF_LINES = 60

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


def _goldens(root: Path, m: _Matcher, report: Report, wanted: set[str], *, update: bool) -> None:
    """Compare (or write) each wanted output's `.expected` on each fixture, and count them.

    Each fixture runs once; outputs don't affect each other, so filtering its windows
    by output is the same as running each output alone.

    Args:
        root: The registry directory, for readable paths.
        m: The matcher under test.
        report: Collects counts, variant coverage and problems.
        wanted: The output ids to check; the rest are skipped.
        update: Write the `.expected` files instead of comparing.
    """
    outs = [(o, out) for o, out in enumerate(m.plan.outputs) if out.output.id in wanted]
    stats = {
        o: report.outputs.setdefault(
            out.output.id,
            OutputReport(variants=dict.fromkeys((v.name for v in out.variants if v.name), False)),
        )
        for o, out in outs
    }
    for log, lines in m.logs.items():
        windows = run(m.plan, lines)
        for o, out in outs:
            own = [w for w in windows if w.output == o]
            stats[o].variants |= {w.variant: True for w in own if w.variant}
            path = golden(log, out.output.id, m.spec.id)
            got = selection(lines, own)
            if problem := _compare(
                path, got, out.output.id, stats[o], label=path.relative_to(root), update=update
            ):
                report.problems.append(f"{log.relative_to(root)}: {problem}")
    report.problems += [
        f"{out.output.id} ({name}): this variant anchors in none of its matcher's fixtures"
        for o, out in outs
        for name, covered in stats[o].variants.items()
        if not covered
    ]


def _compare(
    path: Path, got: str, output_id: str, stats: OutputReport, *, label: Path, update: bool
) -> str | None:
    """Write or compare one golden, counting it as positive or negative.

    Args:
        path: The `.expected` file.
        got: What the output selects now.
        output_id: Names it in a diff.
        stats: Its output's counts.
        label: The `.expected` file as the diff names it, relative to the registry.
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
    diff = list(difflib.unified_diff(want.splitlines(), got.splitlines(), str(label), "actual", lineterm=""))
    if len(diff) > MAX_DIFF_LINES:
        diff = [*diff[:MAX_DIFF_LINES], f"… [{len(diff) - MAX_DIFF_LINES} lines elided] …"]
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


def _cross_fire(root: Path, matchers: list[_Matcher], report: Report, wanted: set[str]) -> None:
    """Wanted outputs firing on another matcher's fixture, run as one Plan: one pass per fixture.

    A fallback is a catch-all by design, so it's not reported. A fixture whose content
    is also one of the other matcher's own fixtures is shared on purpose.

    Args:
        root: The registry directory, for readable paths.
        matchers: Every matcher in the registry: each one's fixtures are run.
        report: Collects the cross-fires.
        wanted: The output ids reported on.
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
                if (
                    out.id not in wanted
                    or other is m
                    or out.role == "fallback"
                    or digests[log] in own[id(other)]
                ):
                    continue
                report.cross_fire.append({"output": out.id, "fixture": str(log.relative_to(root))})


def _runner_trailers(matchers: list[_Matcher], report: Report, wanted: set[str]) -> None:
    """Wanted outputs that fire on a runner's own "job failed" line, and so on every failed job.

    Args:
        matchers: Every matcher in the registry.
        report: Collects problems.
        wanted: The output ids checked.
    """
    for m in matchers:
        for w in run(m.plan, list(RUNNER_TRAILERS)):
            out = m.plan.outputs[w.output].output
            if out.id in wanted and out.role != "fallback":
                line = RUNNER_TRAILERS[w.anchor]
                report.problems.append(
                    f"{out.id}: fires on the runner's own line {line!r}, so on every failed job"
                )


def _load(root: Path, paths: list[Path], report: Report, named: set[str] | None) -> list[_Matcher]:
    """Every matcher in the registry, compiled with its fixtures read.

    Args:
        root: The registry directory, for readable paths.
        paths: Its `matcher.yaml` files.
        report: Collects problems.
        named: The matcher names `--only` picked, whose load errors are reported;
            None reports every one.

    Returns:
        The matchers that loaded, one per name.
    """
    matchers: list[_Matcher] = []
    for path in paths:
        try:
            spec = load_matcher(path, author=True)
        except SetupError as err:
            if named is None or path.parent.name in named:
                report.problems.append(str(err))
            continue
        if spec.id in {m.spec.id for m in matchers}:
            report.problems.append(f"{path.relative_to(root)}: matcher name {spec.id!r} is used twice")
            continue
        matchers.append(_Matcher(path, spec, compile_plan([spec]), _fixtures(root, path, report)))
    return matchers


def _wanted(only: list[str] | None, matchers: list[_Matcher]) -> set[str]:
    """The output ids `--only` names: a matcher's name takes all its outputs.

    Args:
        only: `NAME` or `NAME.OUTPUT` entries, or None for every output.
        matchers: Every matcher in the registry.

    Returns:
        The output ids to check.

    Raises:
        SetupError: If an entry names no matcher or output in the registry.
    """
    outputs = {m.spec.id: [o.output.id for o in m.plan.outputs] for m in matchers}
    if only is None:
        return {o for ids in outputs.values() for o in ids}
    every = {o for ids in outputs.values() for o in ids}
    unknown = [name for name in only if name not in outputs and name not in every]
    if unknown:
        raise SetupError(
            f"--only: no matcher or output named {', '.join(map(repr, unknown))} in this registry"
        )
    return {o for name in only for o in outputs.get(name, [name])}


def check(
    root: Path, *, update: bool = False, policy: Policy | None = None, only: list[str] | None = None
) -> tuple[int, Report]:
    """Run every check over a registry and report, without raising on a failed check.

    Args:
        root: The registry directory.
        update: Write each output's `.expected` from the current output instead of
            comparing. The fixture counts are still enforced.
        policy: The registry's bar; the defaults are the official registry's.
        only: Check only these matchers (`NAME`) or outputs (`NAME.OUTPUT`), with
            every rule; cross-fire still runs them over every fixture in the registry.

    Returns:
        How many matchers were checked, and the report.

    Raises:
        SetupError: If `root` holds no matchers, or `only` names one it doesn't hold.
    """
    policy = policy or Policy()
    paths = matcher_files(root)
    report = Report()
    exempt = _exempt(root, policy)
    if only is None:
        report.problems += [
            f"{policy.exempt}: {name!r} is not a matcher in this registry"
            for name in sorted(exempt - {p.parent.name for p in paths})
        ]
    named = None if only is None else {name.split(".")[0] for name in only}
    matchers = _load(root, paths, report, named)
    wanted = _wanted(only, matchers)
    checked = [m for m in matchers if any(o.output.id in wanted for o in m.plan.outputs)]
    for m in checked:
        _goldens(root, m, report, wanted, update=update)
        for out in m.plan.outputs:
            if out.output.id in wanted:
                stats = report.outputs[out.output.id]
                report.problems += _counts(out.output.id, stats, policy, exempt=m.spec.id in exempt)
    _cross_fire(root, matchers, report, wanted)
    if policy.cross_fire:
        report.problems += [
            f"{c['output']} fires on {c['fixture']}, another matcher's fixture" for c in report.cross_fire
        ]
    _runner_trailers(matchers, report, wanted)
    return len(paths) if only is None else len(checked), report


def verify(
    root: Path,
    *,
    update: bool = False,
    policy: Policy | None = None,
    json_out: Path | None = None,
    only: list[str] | None = None,
) -> int:
    """Run the golden-fixture checks over a registry, or the matchers `only` names.

    Args:
        root: The registry directory.
        update: Write each output's `.expected` instead of comparing.
        policy: The registry's bar; the defaults are the official registry's.
        json_out: Also write the full report here, pass or fail.
        only: Matchers (`NAME`) or outputs (`NAME.OUTPUT`) to check; None for all.

    Returns:
        The number of matchers checked.

    Raises:
        SetupError: Listing every failure.
    """
    n, report = check(root, update=update, policy=policy, only=only)
    if json_out is not None:
        json_out.write_text(report.to_json(), encoding="utf-8")
    if report.problems:
        raise SetupError(f"verify failed ({len(report.problems)}):\n" + "\n".join(report.problems))
    return n
