"""Reading a registry file safely, and the rules a registry adds on top of the schema.

One module for both callers, so the rules can't drift: `matchers index` and
`matchers verify` load with ``author=True`` (the registry's own CI, strict), and
ci-doctor itself loads with ``author=False`` when it fetches a file, because a
self-hosted registry may never have run our CI.

Every field rule (lengths, RE2 cost, ASCII descriptions, a required description
and tag) lives on the engine's spec models and `matchers/models.py`; a file is also
compiled on load, so an undefined `$NAME` or an over-cost regex fails here, not
mid-run. What lives here is reading a file safely, picking the model by its
`schema`, and the one rule that needs to know where the file sits: its id
matching its directory.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ci_doctor.config.schemas.matchers import BARE_ID
from ci_doctor.engine import CompileError, Spec, UnknownSchema, compile_plan, load
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import MATCHER_GLOB, Bundle


class _NoAliasLoader(yaml.SafeLoader):
    """`safe_load` that refuses anchors and aliases: a billion-laughs bomb fits in a tiny file."""

    def compose_node(self, parent: Any, index: Any) -> Any:  # noqa: ANN401 - PyYAML's own untyped signature
        """Reject any node that declares or references an anchor.

        Args:
            parent: The parent node, passed through.
            index: The key or position, passed through.

        Returns:
            The composed node.

        Raises:
            yaml.YAMLError: On an anchor or an alias.
        """
        event = self.peek_event()
        if getattr(event, "anchor", None) is not None:
            raise yaml.YAMLError(f"anchors and aliases are not allowed (line {event.start_mark.line + 1})")
        return super().compose_node(parent, index)


def parse_yaml(text: str, where: str) -> Any:  # noqa: ANN401 - YAML is genuinely any shape until validated
    """Parse one registry document with the alias-free loader.

    Args:
        text: The document.
        where: The file or URL it came from, for the error message.

    Returns:
        The parsed document.

    Raises:
        SetupError: If it isn't valid YAML, or uses an anchor or alias.
    """
    try:
        return yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as err:
        raise SetupError(f"{where}: {err}") from None


def load_yaml(path: Path) -> Any:  # noqa: ANN401 - YAML is genuinely any shape until validated
    """Read and parse one registry file with the alias-free loader.

    Args:
        path: The file.

    Returns:
        The parsed document.

    Raises:
        SetupError: If the file can't be read or isn't valid YAML.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise SetupError(f"{path}: {err}") from None
    return parse_yaml(text, str(path))


def matcher_files(root: Path) -> list[Path]:
    """Every `matcher.yaml` in a registry directory, sorted.

    Args:
        root: The registry directory.

    Returns:
        The files; never empty.

    Raises:
        SetupError: If `root` is not a directory or holds no matchers, so a wrong
            path fails instead of passing as "0 matchers verified".
    """
    if not root.is_dir():
        raise SetupError(f"{root} is not a directory")
    if not (paths := sorted(root.glob(MATCHER_GLOB))):
        raise SetupError(f"{root}: no matchers found (expected {MATCHER_GLOB})")
    return paths


def contained(root: Path, rel: str) -> Path:
    """Resolve a registry-relative path, refusing anything that lands outside the registry.

    Checked on the real path, so a symlink inside the registry can't point at
    `/etc/...` even though its text passes the index's path rules.

    Args:
        root: The registry directory.
        rel: A path relative to it.

    Returns:
        The resolved path.

    Raises:
        SetupError: If the resolved path is outside `root`.
    """
    real = (root / rel).resolve()
    if not real.is_relative_to(root.resolve()):
        raise SetupError(f"{rel!r} resolves outside the registry {root}")
    return real


def matcher_from(data: Any, where: str, *, dirs: tuple[str, str] | None = None) -> Spec:  # noqa: ANN401 - parsed YAML
    """Validate and compile one parsed matcher document, still under its bare id.

    Args:
        data: The parsed `matcher.yaml`.
        where: The file or URL it came from, for error messages.
        dirs: ``(ecosystem, id)`` directory names to match against, at author time.

    Returns:
        The matcher, with its bare id.

    Raises:
        SetupError: Naming `where` and everything wrong with it, including a schema
            version this ci-doctor doesn't know.
    """
    try:
        m = load(data)
        compile_plan([m])
    except (UnknownSchema, CompileError) as err:
        raise SetupError(f"{where}: {err}") from None
    except ValidationError as err:
        raise SetupError(f"{where}: {err}") from None
    if not re.fullmatch(BARE_ID, m.id):
        raise SetupError(f"{where}: id {m.id!r} must be a bare name matching {BARE_ID}")
    if dirs is not None and m.id != dirs[1]:
        raise SetupError(f"{where}: id {m.id!r} must equal its directory name {dirs[1]!r}")
    return m


def load_matcher(path: Path, *, author: bool = False) -> Spec:
    """Load and validate one `matcher.yaml` from disk.

    Args:
        path: The file, at `matchers/<eco>/<id>/matcher.yaml` in its registry.
        author: Also check that the id matches its directory, as `matchers index` /
            `verify` do.

    Returns:
        The matcher, with its bare id.

    Raises:
        SetupError: Naming the file and what is wrong with it.
    """
    dirs = (path.parent.parent.name, path.parent.name) if author else None
    return matcher_from(load_yaml(path), str(path), dirs=dirs)


def bundle_from(data: Any, where: str) -> Bundle:  # noqa: ANN401 - parsed YAML
    """Validate one parsed bundle document.

    Args:
        data: The parsed `bundles/<id>.yaml`.
        where: The file or URL it came from, for error messages.

    Returns:
        The bundle.

    Raises:
        SetupError: Naming `where` and what is wrong with it.
    """
    try:
        return Bundle.model_validate(data)
    except ValidationError as err:
        raise SetupError(f"{where}: {err}") from None


def load_bundle(path: Path, *, author: bool = False) -> Bundle:
    """Load and validate one `bundles/<id>.yaml` from disk.

    Args:
        path: The file.
        author: Also check that the id matches the file name.

    Returns:
        The bundle.

    Raises:
        SetupError: Naming the file and what is wrong with it.
    """
    bundle = bundle_from(load_yaml(path), str(path))
    if author and bundle.id != path.stem:
        raise SetupError(f"{path}: id {bundle.id!r} must equal its file name {path.stem!r}")
    return bundle
