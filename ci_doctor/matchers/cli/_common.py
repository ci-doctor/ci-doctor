"""What several `matchers` commands share: the `--config` option and the config they read."""

from pathlib import Path

import httpx
import typer

from ci_doctor.config.loader import load_config
from ci_doctor.config.schema import MatchersConfig
from ci_doctor.matchers.registry import resolve

CONFIG = typer.Option(
    Path(".ci-doctor.yml"), "--config", "-f", help="The config file to read and edit; created if missing."
)
ROOT = typer.Argument(Path("."), help="The registry directory (the one holding index.yaml).")


def effective(path: Path) -> MatchersConfig:
    """The `matchers:` block as `analyze -f <path>` would see it; a missing file adds nothing.

    Args:
        path: The config file, which may not exist.

    Returns:
        The merged block.

    Raises:
        SetupError: If the config does not validate.
    """
    return load_config([path] if path.is_file() else []).matchers


def via(cfg: MatchersConfig, include: list[str], http: httpx.Client) -> dict[str, str]:
    """Which top-level entry brings in each matcher.

    Args:
        cfg: The effective block (for its registries).
        include: The refs to expand.
        http: The command's HTTP client.

    Returns:
        `alias/name` -> `"direct"` or the bundle; empty when `include` is.
    """
    if not include:
        return {}
    return resolve(MatchersConfig(registries=cfg.registries, include=include), http).matchers
