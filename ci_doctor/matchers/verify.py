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
from pathlib import Path

from ci_doctor.config.loader import default_config
from ci_doctor.config.schema import MatcherConfig
from ci_doctor.core.denoise import denoise
from ci_doctor.core.extract import extract, fired
from ci_doctor.core.models import walk_sections
from ci_doctor.errors import SetupError
from ci_doctor.matchers.validate import load_matcher
from ci_doctor.providers.registry import segmenter_for_log

EXEMPT = "verify-exempt.txt"
MIN_POSITIVE, MIN_NEGATIVE = 3, 2
MAX_FIXTURE_BYTES = 200 * 1024


def read_fixture(path: Path) -> list[str]:
    """A fixture log as the extractor receives it in a real run.

    Args:
        path: The `.log` file.

    Returns:
        Every section's lines in log order, framing stripped and denoised.
    """
    cfg = default_config()
    raw = path.read_text(errors="replace")
    sections = segmenter_for_log(raw, cfg.ci).segment(raw)
    return denoise([line.text for sec in walk_sections(sections) for line in sec.lines], cfg.denoise)


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
    lines = (line.strip() for line in path.read_text().splitlines())
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
        where = log.relative_to(root)
        if log.stat().st_size > MAX_FIXTURE_BYTES:
            yield f"{where}: {log.stat().st_size} bytes, over {MAX_FIXTURE_BYTES}"
            continue
        got = matcher_output(read_fixture(log), matcher)
        expected_path = log.with_suffix(".expected")
        if update:
            expected_path.write_text(got)
        elif not expected_path.is_file():
            yield f"{where}: no {expected_path.name}; run `ci-doctor matchers verify --update`"
            continue
        want = expected_path.read_text()
        if want:
            positive += 1
        else:
            negative += 1
        if got != want:
            diff = difflib.unified_diff(
                want.splitlines(), got.splitlines(), str(expected_path.name), "actual", lineterm=""
            )
            yield f"{where}: output differs from {expected_path.name}\n" + "\n".join(diff)

    if exempt:
        if positive >= MIN_POSITIVE and negative >= MIN_NEGATIVE:
            yield f"{matcher.id}: meets {MIN_POSITIVE} + {MIN_NEGATIVE} fixtures, remove it from {EXEMPT}"
        elif positive < 1:
            yield f"{matcher.id}: needs at least 1 positive fixture (exempt from the full minimum)"
    elif positive < MIN_POSITIVE or negative < MIN_NEGATIVE:
        yield (
            f"{matcher.id}: has {positive} positive and {negative} negative fixtures, "
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
    if not root.is_dir():
        raise SetupError(f"{root} is not a directory")
    paths = sorted(root.glob("matchers/*/*/matcher.yaml"))
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
