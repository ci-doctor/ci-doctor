"""Reading a registry file safely, and the rules a registry adds on top of the schema.

One module for both callers, so the rules can't drift: `matchers index` and
`matchers verify` load with ``author=True`` (the registry's own CI, strict), and
ci-doctor itself loads with ``author=False`` when it fetches a file, because a
self-hosted registry may never have run our CI.

Every field rule (lengths, RE2 cost, ASCII descriptions, the bare id, a required
description and tag) lives on the models in `config/schema.py` and
`matchers/models.py`. What lives here is reading a file safely, and the one rule
that needs to know where the file sits: its id and ecosystem matching their
directories. Writing one safely (`atomic_write`) is here too, for `index.yaml` and
the user's config alike.
"""

import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ci_doctor.config.schema import MatcherConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import MATCHER_GLOB, Bundle, RegistryMatcher


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


def atomic_write(path: Path, text: str) -> None:
    """Replace a file's contents in one step, leaving it the same file.

    A temp file renamed over the target, so nothing ever sees half a file. The
    rename lands on the real path, so a symlink (a dotfiles `.ci-doctor.yml`) stays
    a symlink, and the file keeps its mode; a new one gets `0o666 & ~umask` like
    any file the user's tools create, not `mkstemp`'s 0600.

    Args:
        path: The file; created if missing.
        text: Its new contents, written as UTF-8.

    Raises:
        SetupError: If the file can't be written, e.g. its directory is missing.
    """
    real = path.resolve()
    try:
        mode = stat.S_IMODE(real.stat().st_mode)
    except FileNotFoundError:
        umask = os.umask(0)  # the only way to read it is to set it
        os.umask(umask)
        mode = 0o666 & ~umask
    try:
        fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.")
    except OSError as err:
        raise SetupError(f"{path}: cannot write: {err.strerror or err}") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException as err:
        Path(tmp).unlink(missing_ok=True)
        if isinstance(err, OSError):
            raise SetupError(f"{path}: cannot write: {err.strerror or err}") from None
        raise


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


def matcher_from(data: Any, where: str, *, dirs: tuple[str, str] | None = None) -> MatcherConfig:  # noqa: ANN401 - parsed YAML
    """Validate one parsed matcher document, still under its bare id.

    Args:
        data: The parsed `matcher.yaml`.
        where: The file or URL it came from, for error messages.
        dirs: ``(ecosystem, id)`` directory names to match against, at author time.

    Returns:
        The matcher, with its bare id.

    Raises:
        SetupError: Naming `where` and everything wrong with it.
    """
    try:
        m = RegistryMatcher.model_validate(data)
    except ValidationError as err:
        raise SetupError(f"{where}: {err}") from None
    if dirs is None:
        return m
    eco, name = dirs
    problems = []
    if m.id != name:
        problems.append(f"id {m.id!r} must equal its directory name {name!r}")
    if m.classification[0] != eco:
        problems.append(
            f"first classification tag {m.classification[0]!r} must equal its ecosystem directory {eco!r}"
        )
    if problems:
        raise SetupError(f"{where}: " + "; ".join(problems))
    return m


def load_matcher(path: Path, *, author: bool = False) -> MatcherConfig:
    """Load and validate one `matcher.yaml` from disk.

    Args:
        path: The file, at `matchers/<eco>/<id>/matcher.yaml` in its registry.
        author: Also check that the id and ecosystem match the directories, as
            `matchers index` / `verify` do.

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
