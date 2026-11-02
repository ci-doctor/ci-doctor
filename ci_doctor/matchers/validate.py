"""Reading a registry file safely, and the rules a registry adds on top of the schema.

One module for both callers, so the rules can't drift: `matchers index` and
`matchers verify` load with ``author=True`` (the registry's own CI, strict), and
ci-doctor itself loads with ``author=False`` when it fetches a file, because a
self-hosted registry may never have run our CI.

Field-level rules (lengths, RE2 cost, ASCII descriptions) live on the models in
`config/schema.py` and `matchers/models.py`. What lives here is what only makes
sense for a file in a registry: the bare id (it becomes `alias/id` later, which
the model's own id pattern must still accept), a required description and tag,
and the id and ecosystem matching their directories.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ci_doctor.config.schema import MatcherConfig
from ci_doctor.errors import SetupError
from ci_doctor.matchers.models import BARE_ID, Bundle


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


def load_yaml(path: Path) -> Any:  # noqa: ANN401 - YAML is genuinely any shape until validated
    """Parse one registry file with the alias-free loader.

    Args:
        path: The file.

    Returns:
        The parsed document.

    Raises:
        SetupError: If the file can't be read or isn't valid YAML.
    """
    try:
        return yaml.load(path.read_text(), Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader subclass
    except (OSError, yaml.YAMLError) as err:
        raise SetupError(f"{path}: {err}") from None


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


def load_matcher(path: Path, *, author: bool = False) -> MatcherConfig:
    """Load and validate one `matcher.yaml`.

    Args:
        path: The file, at `matchers/<eco>/<id>/matcher.yaml` in its registry.
        author: Also check that the id and ecosystem match the directories, as
            `matchers index` / `verify` do.

    Returns:
        The matcher, with its bare id.

    Raises:
        SetupError: Naming the file and what is wrong with it.
    """
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise SetupError(f"{path}: a matcher file must be a mapping")
    problems = []
    mid = data.get("id")
    if not isinstance(mid, str) or not BARE_ID.match(mid):
        problems.append(f"id {mid!r} must match {BARE_ID.pattern}")
    if not data.get("description"):
        problems.append("description is required")
    tags = data.get("classification")
    if not tags:
        problems.append("classification needs at least one tag, the ecosystem first")
    if author and isinstance(mid, str) and mid != path.parent.name:
        problems.append(f"id {mid!r} must equal its directory name {path.parent.name!r}")
    if author and isinstance(tags, list) and tags and tags[0] != path.parent.parent.name:
        problems.append(
            f"first classification tag {tags[0]!r} must equal its ecosystem directory {path.parent.parent.name!r}"
        )
    if problems:
        raise SetupError(f"{path}: " + "; ".join(problems))
    try:
        return MatcherConfig.model_validate(data)
    except ValidationError as err:
        raise SetupError(f"{path}: {err}") from None


def load_bundle(path: Path, *, author: bool = False) -> Bundle:
    """Load and validate one `bundles/<id>.yaml`.

    Args:
        path: The file.
        author: Also check that the id matches the file name.

    Returns:
        The bundle.

    Raises:
        SetupError: Naming the file and what is wrong with it.
    """
    data = load_yaml(path)
    try:
        bundle = Bundle.model_validate(data)
    except ValidationError as err:
        raise SetupError(f"{path}: {err}") from None
    if author and bundle.id != path.stem:
        raise SetupError(f"{path}: id {bundle.id!r} must equal its file name {path.stem!r}")
    return bundle
