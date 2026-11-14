"""The setup dry run behind `ci-doctor config --validate` and the MCP `check_setup` tool."""

import os
from pathlib import Path

from ci_doctor.config.loader import load_config
from ci_doctor.config.schemas import Config
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import load as load_matchers
from ci_doctor.providers.tokens import read_token


def validate(paths: list[Path]) -> str:
    """Load the config and every selected matcher; summarize the setup in one line.

    Args:
        paths: The `-f` files, lowest precedence first.

    Returns:
        A one-line summary, including whether the CI system's API token is set.

    Raises:
        SetupError: On the first setup problem.
    """
    cfg = load_config(paths)
    matchers = load_matchers(cfg.matchers)
    registries = len({Ref.parse(r).alias for r in cfg.matchers.include})
    summary = f"config ok: ci={cfg.ci}, scm={cfg.scm_vendor}, {len(matchers)} matchers from {registries} registries."
    return f"{summary} {_token(cfg)}".rstrip()


def _token(cfg: Config) -> str:
    """Whether the CI system's API token is set, in words the user can act on. Never the token."""
    block = getattr(cfg, cfg.ci, None)
    env = getattr(block, "token_env", None)
    if block is None or env is None:
        return ""
    if read_token(block.token_file, env, os.environ):
        return "API token: set."
    return f"No API token: set {env} to read private projects and job logs."
