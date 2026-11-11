"""The author's side of a registry: building and checking its `index.yaml`.

The index is generated, committed, and checked in the registry's CI (`--check`),
so it never ships stale. So is one bundle per ecosystem folder
(`bundles/<ecosystem>.yaml`, everything under `matchers/<ecosystem>/`), so a new
matcher can't be left out of its bundle; curated bundles (`all`, a team's pick)
stay hand-written and may not take an ecosystem's name. Generation is deterministic — sorted entries, one block
each — so two PRs touching different matchers merge cleanly, and a conflict is
fixed by rerunning the command.
"""

import hashlib
from pathlib import Path

import yaml

from ci_doctor.engine import Spec
from ci_doctor.errors import SetupError
from ci_doctor.lib.files import atomic_write
from ci_doctor.matchers.models import (
    BUNDLE_GLOB,
    GENERATED,
    GENERATED_PREFIX,
    INDEX,
    MAX_BUNDLE_DEPTH,
    Bundle,
    Index,
)
from ci_doctor.matchers.validate import load_bundle, load_matcher, matcher_files


class _IndexDumper(yaml.SafeDumper):
    """Block mappings, one entry per block; tag lists inline (`[python, test]`)."""


_IndexDumper.add_representer(
    list, lambda dumper, data: dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
)


def _entry(root: Path, path: Path, description: str) -> dict:
    """One index entry for a file: where it is and what it says it is.

    Args:
        root: The registry directory.
        path: The file.
        description: Its description.

    Returns:
        The entry, in index key order.
    """
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "description": description,
    }


def _matcher_entry(root: Path, path: Path, m: Spec) -> dict:
    """A matcher's index entry: what a client needs to choose and skip it without fetching it.

    Args:
        root: The registry directory.
        path: Its `matcher.yaml`.
        m: The loaded matcher.

    Returns:
        The entry, in index key order.
    """
    entry = _entry(root, path, m.description) | {
        "schema": m.schema_version,
        "tags": list(m.tags),
        "ecosystem": path.parent.parent.name,
    }
    if m.outputs is not None:
        entry["outputs"] = list(m.outputs)
    return entry


def _generated(ecosystems: dict[str, list[str]]) -> dict[str, str]:
    """One bundle per ecosystem folder, as the text `matchers index` writes.

    Args:
        ecosystems: Each ecosystem folder's matcher names.

    Returns:
        `bundles/<ecosystem>.yaml` text, by ecosystem.
    """
    out = {}
    for eco, names in sorted(ecosystems.items()):
        doc = {"id": eco, "description": f"Every matcher in the {eco} ecosystem.", "include": sorted(names)}
        body = yaml.dump(doc, Dumper=_IndexDumper, sort_keys=False, default_flow_style=False, width=4096)
        out[eco] = GENERATED.format(ecosystem=eco) + "\n" + body
    return out


def _bundle_problems(bundles: dict[str, Bundle], matchers: set[str]) -> list[str]:
    """Unknown names, cycles and over-deep nesting across a registry's bundles.

    Args:
        bundles: Every bundle by name.
        matchers: Every matcher name.

    Returns:
        One message per problem.
    """
    problems = [
        f"bundle {b.id!r} includes unknown names {unknown}"
        for b in bundles.values()
        if (unknown := sorted(n for n in b.include if n not in bundles and n not in matchers))
    ]
    return problems + _nesting_problems(bundles)


def _nesting_problems(bundles: dict[str, Bundle]) -> list[str]:
    """Cycles and over-deep nesting across a registry's bundles.

    Args:
        bundles: Every bundle by name.

    Returns:
        One message per bundle nested over `MAX_BUNDLE_DEPTH`, or the first cycle
        found (depth is meaningless past it, so the walk stops there).
    """
    problems = []
    depth: dict[str, int] = {}

    def walk(name: str, stack: tuple[str, ...]) -> int:
        if name in stack:
            raise SetupError(f"bundle cycle: {' -> '.join((*stack, name))}")
        if name not in depth:
            subs = [n for n in bundles[name].include if n in bundles]
            depth[name] = 1 + max((walk(n, (*stack, name)) for n in subs), default=0)
        return depth[name]

    for name in sorted(bundles):
        try:
            if walk(name, ()) > MAX_BUNDLE_DEPTH:
                problems.append(f"bundle {name!r} nests {depth[name]} deep, over {MAX_BUNDLE_DEPTH}")
        except SetupError as err:
            problems.append(str(err))
            break
    return problems


def _is_generated(path: Path) -> bool:
    """Whether `matchers index` wrote this bundle file."""
    return path.read_text(encoding="utf-8").startswith(GENERATED_PREFIX)


def _load_matchers(root: Path, problems: list[str]) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Every matcher's index entry, and each ecosystem folder's matcher names.

    Args:
        root: The registry directory.
        problems: Collects every file that fails, and duplicate names.

    Returns:
        Index entries by name, and names by ecosystem.
    """
    matchers: dict[str, dict] = {}
    ecosystems: dict[str, list[str]] = {}
    for path in matcher_files(root):
        try:
            m = load_matcher(path, author=True)
        except SetupError as err:
            problems.append(str(err))
            continue
        if m.id in matchers:
            problems.append(f"{path}: matcher name {m.id!r} is used twice")
        matchers[m.id] = _matcher_entry(root, path, m)
        ecosystems.setdefault(path.parent.parent.name, []).append(m.id)
    return matchers, ecosystems


def _generated_bundles(
    generated: dict[str, str], matchers: dict[str, dict], problems: list[str]
) -> tuple[dict[str, Bundle], dict[str, dict]]:
    """The ecosystem bundles this run generates, and their index entries.

    Args:
        generated: Each ecosystem bundle's text.
        matchers: Matcher entries by name, to catch an ecosystem named like a matcher.
        problems: Collects such clashes.

    Returns:
        Bundles and index entries, by ecosystem.
    """
    bundles, entries = {}, {}
    for eco, text in generated.items():
        if eco in matchers:
            problems.append(f"ecosystem {eco!r} has the same name as a matcher")
        bundles[eco] = Bundle.model_validate(yaml.safe_load(text))
        entries[eco] = {
            "path": f"bundles/{eco}.yaml",
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "description": bundles[eco].description,
        }
    return bundles, entries


def _load_bundles(
    root: Path, generated: dict[str, str], matchers: dict[str, dict], problems: list[str]
) -> dict[str, tuple[Bundle, dict]]:
    """The hand-written bundles: every bundle file `matchers index` did not generate.

    Args:
        root: The registry directory.
        generated: The ecosystem bundles, whose names a hand-written file may not take.
        matchers: Matcher entries by name.
        problems: Collects every file that fails, and names already taken by a matcher.

    Returns:
        Each bundle and its index entry, by name.
    """
    out = {}
    for path in sorted(root.glob(BUNDLE_GLOB)):
        if path.stem in generated or _is_generated(path):
            continue  # rewritten from its folder, or stale and deleted on write
        try:
            b = load_bundle(path, author=True)
        except SetupError as err:
            problems.append(str(err))
            continue
        if b.id in matchers:
            problems.append(f"{path}: name {b.id!r} is already a matcher")
        out[b.id] = (b, _entry(root, path, b.description))
    return out


def build_index(root: Path) -> tuple[str, Index, dict[str, str]]:
    """Validate every matcher and bundle in a registry and render its `index.yaml`.

    Args:
        root: The registry directory.

    Returns:
        The index text, the index it parses back to, and each generated ecosystem
        bundle's text by ecosystem. The same tree always gives the same bytes.

    Raises:
        SetupError: Listing every file or cross-file rule that fails.
    """
    problems: list[str] = []
    matchers, ecosystems = _load_matchers(root, problems)
    generated = _generated(ecosystems)
    bundles, bundle_entries = _generated_bundles(generated, matchers, problems)
    for name, (b, entry) in _load_bundles(root, generated, matchers, problems).items():
        bundles[name], bundle_entries[name] = b, entry

    problems += _bundle_problems(bundles, set(matchers))
    if problems:
        raise SetupError("registry is invalid:\n  " + "\n  ".join(problems))

    doc = {
        "schema": 1,
        "matchers": dict(sorted(matchers.items())),
        "bundles": dict(sorted(bundle_entries.items())),
    }
    index = Index.model_validate(doc)  # what we write, ci-doctor must be able to read back
    text = yaml.dump(doc, Dumper=_IndexDumper, sort_keys=False, default_flow_style=False, width=4096)
    return text, index, generated


def _stale_generated(root: Path, generated: dict[str, str]) -> list[Path]:
    """Generated bundles whose ecosystem folder is gone.

    Args:
        root: The registry directory.
        generated: The bundles this run generates, by ecosystem.

    Returns:
        Their paths.
    """
    return [p for p in sorted(root.glob(BUNDLE_GLOB)) if p.stem not in generated and _is_generated(p)]


def write_index(root: Path, *, check: bool = False) -> int:
    """Write `index.yaml` and the ecosystem bundles, or with `check` verify they are current.

    Args:
        root: The registry directory.
        check: Compare instead of writing.

    Returns:
        The number of entries in the index.

    Raises:
        SetupError: If the registry is invalid, or `check` finds a file stale.
    """
    text, index, generated = build_index(root)
    files = {root / INDEX: text} | {root / "bundles" / f"{eco}.yaml": body for eco, body in generated.items()}
    stale = _stale_generated(root, generated)
    if check:
        out_of_date = [
            p for p, body in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != body
        ]
        if out_of_date or stale:
            names = ", ".join(str(p.relative_to(root)) for p in [*out_of_date, *stale])
            raise SetupError(f"out of date: {names}. Run `ci-doctor matchers index {root}`")
    else:
        (root / "bundles").mkdir(exist_ok=True)
        for path, body in files.items():
            atomic_write(path, body)
        for path in stale:
            path.unlink()
    return len(index.matchers) + len(index.bundles)
