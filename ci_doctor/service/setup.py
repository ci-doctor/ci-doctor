"""The setup dry run behind `ci-doctor config --validate`."""

from pathlib import Path

from ci_doctor.config.loader import load_config
from ci_doctor.matchers.models import Ref
from ci_doctor.matchers.registry import load as load_matchers


def validate(paths: list[Path]) -> str:
    """Load the config and every selected matcher; return a one-line summary.

    Raises:
        SetupError: On the first setup problem.
    """
    cfg = load_config(paths)
    matchers = load_matchers(cfg.matchers)
    registries = len({Ref.parse(r).alias for r in cfg.matchers.include})
    return f"config ok: ci={cfg.ci}, scm={cfg.scm_vendor}, {len(matchers)} matchers from {registries} registries."
