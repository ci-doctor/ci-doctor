"""Golden-fixture tests for a registry's matchers, run with ci-doctor's own extraction.

Each fixture is a job log plus a `.expected` file holding exactly what the matcher
alone extracts from it; an empty `.expected` means it must not fire. `--update`
writes the `.expected` files and the reviewer reads them in the PR diff, so "what
this matcher catches" is reviewed as plain text. No LLM is involved.

A log is read the way `ci-doctor analyze <file>` reads one — runner framing
stripped by the segmenter, then `denoise` — so a raw GitHub or GitLab log is a
valid fixture as it is, and a `^` anchor sees what it will see in production.
"""

import difflib
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from ci_doctor.config.loader import default_config
from ci_doctor.config.schemas.matcher import MatcherConfig
from ci_doctor.core.denoise import denoise
from ci_doctor.core.extract import extract, fired
from ci_doctor.core.models import walk_sections
from ci_doctor.errors import SetupError
from ci_doctor.matchers.validate import load_matcher, matcher_files
from ci_doctor.providers.registry import segmenter_for_log

# Plaster: remove EXEMPT and its logic (_exempt, the `exempt` branch) once every matcher meets the minimum.
EXEMPT = "verify-exempt.txt"
MIN_POSITIVE, MIN_NEGATIVE = 3, 2
MAX_FIXTURE_BYTES = 200 * 1024


#: The shipped config, read once: it is the same for every fixture, and it is all a
#: fixture is read with (no repo file, no env), so a verify run is reproducible anywhere.
_shipped = cache(default_config)


def read_fixture(path: Path) -> list[str]:
    """A fixture log as the extractor receives it in a real run.

    Args:
        path: The `.log` file.

    Returns:
        Every content line in log order, framing stripped and denoised.
    """
    cfg = _shipped()
    raw = path.read_text(encoding="utf-8", errors="replace")
    sections = segmenter_for_log(raw, cfg.ci).segment(raw)
    # Log order, not tree order: a segmenter files some lines under a synthetic
    # trailer that walks before later steps, which would skew before/after and `end`.
    lines = sorted((line for sec in walk_sections(sections) for line in sec.lines), key=lambda ln: ln.number)
    return denoise([line.text for line in lines], cfg.denoise)


def matcher_output(lines: list[str], matcher: MatcherConfig) -> str:
    """What one matcher alone selects, in `.expected` form.

    `extract()` falls back to the whole log when nothing matched, which would make a
    negative fixture compare against the log itself; no window means empty here.

    Args:
        lines: The fixture, from :func:`read_fixture`.
        matcher: The matcher under test.

    Returns:
        The selected lines with elision markers, newline-terminated, or "" when the
        matcher has no window.
    """
    if not fired(lines, [matcher]):
        return ""
    return "\n".join(extract(lines, [matcher], tail_lines=0)) + "\n"


def _exempt(root: Path) -> set[str]:
    """Names grandfathered to one positive fixture; blank lines and `#` comments skipped.

    Args:
        root: The registry directory.

    Returns:
        The listed names.
    """
    path = root / EXEMPT
    if not path.is_file():
        return set()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def _check_matcher(root: Path, path: Path, *, exempt: bool, update: bool) -> Iterator[str]:
    """Every failure for one matcher: the file itself, its fixture count, each fixture.

    Args:
        root: The registry directory, for readable paths in messages.
        path: Its `matcher.yaml`.
        exempt: Whether it is listed in `verify-exempt.txt`.
        update: Write `.expected` files instead of comparing.

    Yields:
        One message per failure.
    """
    matcher = load_matcher(path, author=True)
    positive = negative = 0
    for log in sorted((path.parent / "fixtures").glob("*.log")):
        problem, is_positive = _check_fixture(root, log, matcher, update=update)
        if is_positive is True:
            positive += 1
        elif is_positive is False:
            negative += 1
        if problem:
            yield problem
    yield from _count_problems(matcher.id, positive, negative, exempt=exempt)


def _check_fixture(
    root: Path, log: Path, matcher: MatcherConfig, *, update: bool
) -> tuple[str | None, bool | None]:
    """Run one fixture and compare it with its `.expected` file.

    Args:
        root: The registry directory, for readable paths in messages.
        log: The fixture's `.log` file.
        matcher: The matcher under test.
        update: Write the `.expected` file instead of comparing.

    Returns:
        The failure message, or None; and whether the fixture counts as positive
        (True), negative (False) or not at all (None: oversized or missing its
        `.expected`).
    """
    where = log.relative_to(root)
    size = log.stat().st_size
    if size > MAX_FIXTURE_BYTES:
        return f"{where}: {size} bytes, over {MAX_FIXTURE_BYTES}", None
    got = matcher_output(read_fixture(log), matcher)
    expected_path = log.with_suffix(".expected")
    if update:
        expected_path.write_text(got, encoding="utf-8")
    elif not expected_path.is_file():
        return f"{where}: no {expected_path.name}; run `ci-doctor matchers verify --update`", None
    want = expected_path.read_text(encoding="utf-8")
    if got == want:
        return None, bool(want)
    diff = difflib.unified_diff(
        want.splitlines(), got.splitlines(), str(expected_path.name), "actual", lineterm=""
    )
    return f"{where}: output differs from {expected_path.name}\n" + "\n".join(diff), bool(want)


def _count_problems(matcher_id: str, positive: int, negative: int, *, exempt: bool) -> Iterator[str]:
    """The fixture-count rule for one matcher.

    Args:
        matcher_id: For messages.
        positive: Fixtures it fires on.
        negative: Fixtures it must stay silent on.
        exempt: Whether it is listed in `verify-exempt.txt`.

    Yields:
        One message when the counts break the rule.
    """
    if exempt:
        if positive >= MIN_POSITIVE and negative >= MIN_NEGATIVE:
            yield f"{matcher_id}: meets {MIN_POSITIVE} + {MIN_NEGATIVE} fixtures, remove it from {EXEMPT}"
        elif positive < 1:
            yield f"{matcher_id}: needs at least 1 positive fixture (exempt from the full minimum)"
    elif positive < MIN_POSITIVE or negative < MIN_NEGATIVE:
        yield (
            f"{matcher_id}: has {positive} positive and {negative} negative fixtures, "
            f"needs at least {MIN_POSITIVE} and {MIN_NEGATIVE}"
        )


def verify(root: Path, *, update: bool = False) -> int:
    """Run the golden-fixture checks over a whole registry.

    Args:
        root: The registry directory.
        update: Write each fixture's `.expected` from the current output instead of
            comparing. The fixture counts are still enforced.

    Returns:
        The number of matchers checked.

    Raises:
        SetupError: Listing every failure.
    """
    paths = matcher_files(root)
    exempt = _exempt(root)
    problems = [
        f"{EXEMPT}: {name!r} is not a matcher in this registry"
        for name in sorted(exempt - {p.parent.name for p in paths})
    ]
    for path in paths:
        try:
            problems += _check_matcher(root, path, exempt=path.parent.name in exempt, update=update)
        except SetupError as err:
            problems.append(str(err))
    if problems:
        raise SetupError(f"verify failed ({len(problems)}):\n" + "\n".join(problems))
    return len(paths)
