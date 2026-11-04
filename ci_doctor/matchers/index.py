"""The author's side of a registry: building and checking its `index.yaml`.

The index is generated, committed, and checked in the registry's CI (`--check`),
so it never ships stale. Generation is deterministic — sorted entries, one block
each — so two PRs touching different matchers merge cleanly, and a conflict is
fixed by rerunning the command.
"""

import hashlib
from pathlib import Path

import yaml

from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import BUNDLE_GLOB, INDEX, MATCHER_GLOB, MAX_BUNDLE_DEPTH, Bundle, Index
from ci_doctor.matchers.validate import atomic_write, load_bundle, load_matcher


class _IndexDumper(yaml.SafeDumper):
    """Block mappings, one entry per block; tag lists inline (`[python, test]`)."""


_IndexDumper.add_representer(
    list, lambda dumper, data: dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
)


def _entry(root: Path, path: Path, description: str, classification: list[str] | None = None) -> dict:
    """One index entry for a file.

    Args:
        root: The registry directory.
        path: The file.
        description: Its description.
        classification: Its tags, for a matcher.

    Returns:
        The entry, in index key order.
    """
    entry = {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "description": description,
    }
    if classification is not None:
        entry["classification"] = classification
    return entry


def _bundle_problems(bundles: dict[str, Bundle], matchers: set[str]) -> list[str]:
    """Unknown names, cycles and over-deep nesting across a registry's bundles.

    Args:
        bundles: Every bundle by name.
        matchers: Every matcher name.

    Returns:
        One message per problem.
    """
    problems = []
    for b in bundles.values():
        if unknown := sorted(n for n in b.include if n not in bundles and n not in matchers):
            problems.append(f"bundle {b.id!r} includes unknown names {unknown}")

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


def build_index(root: Path) -> tuple[str, Index]:
    """Validate every matcher and bundle in a registry and render its `index.yaml`.

    Args:
        root: The registry directory.

    Returns:
        The index text, and the index it parses back to. The same tree always
        gives the same bytes.

    Raises:
        SetupError: Listing every file or cross-file rule that fails.
    """
    if not root.is_dir():
        raise SetupError(f"{root} is not a directory")
    problems: list[str] = []
    matchers: dict[str, dict] = {}
    bundles: dict[str, Bundle] = {}
    bundle_entries: dict[str, dict] = {}

    for path in sorted(root.glob(MATCHER_GLOB)):
        try:
            m = load_matcher(path, author=True)
        except SetupError as err:
            problems.append(str(err))
            continue
        if m.id in matchers:
            problems.append(f"{path}: matcher name {m.id!r} is used twice")
        matchers[m.id] = _entry(root, path, m.description or "", list(m.classification))

    for path in sorted(root.glob(BUNDLE_GLOB)):
        try:
            b = load_bundle(path, author=True)
        except SetupError as err:
            problems.append(str(err))
            continue
        if b.id in matchers:
            problems.append(f"{path}: name {b.id!r} is already a matcher")
        bundles[b.id] = b
        bundle_entries[b.id] = _entry(root, path, b.description)

    problems += _bundle_problems(bundles, set(matchers))
    if problems:
        raise SetupError("registry is invalid:\n  " + "\n  ".join(problems))

    doc = {
        "schema": 1,
        "matchers": dict(sorted(matchers.items())),
        "bundles": dict(sorted(bundle_entries.items())),
    }
    index = Index.model_validate(doc)  # what we write, ci-doctor must be able to read back
    return yaml.dump(doc, Dumper=_IndexDumper, sort_keys=False, default_flow_style=False, width=4096), index


def write_index(root: Path, *, check: bool = False) -> int:
    """Write `index.yaml`, or with `check` verify the committed one is current.

    Args:
        root: The registry directory.
        check: Compare instead of writing.

    Returns:
        The number of entries in the index.

    Raises:
        SetupError: If the registry is invalid, or `check` finds the index stale.
    """
    text, index = build_index(root)
    target = root / INDEX
    if check:
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            raise SetupError(f"{target} is out of date. Run `ci-doctor matchers index {root}`")
    else:
        atomic_write(target, text)
    return len(index.matchers) + len(index.bundles)
