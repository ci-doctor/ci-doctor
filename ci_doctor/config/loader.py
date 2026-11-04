"""Layered config loading: schema defaults < defaults.yml < repo file < env < flags.

Mappings deep-merge; lists and scalars replace, so `matchers.include` in a repo
file is the whole selection, not an addition to the default one. Unknown
keys in a config *file* are a hard error (schema uses ``extra="forbid"``). Env vars
are more permissive by necessity (see :func:`_env_overlay`).
"""

import logging
import os
from collections.abc import Iterable, Mapping
from copy import deepcopy
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ci_doctor.config.schema import Config
from ci_doctor.errors import SetupError

log = logging.getLogger(__name__)

#: libyaml's safe loader when the wheel ships it (~10x faster than pure Python, and
#: `defaults.yml` is parsed on every run), else pure Python.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_ENV_PREFIX = "CI_DOCTOR_"
_TOP_LEVEL = set(Config.model_fields)  # provider, gitlab, llm, ...
_DEFAULT_REPO_CONFIG = Path(".ci-doctor.yml")


def _deep_merge(base: dict, over: dict, path: str = "") -> dict:
    """Recursively overlay ``over`` onto ``base``.

    Args:
        base: The lower-precedence layer. Not mutated.
        over: The higher-precedence layer.
        path: Dotted prefix of the current node, for override warnings.

    Returns:
        A new dict: mappings deep-merged, everything else (lists included)
        replaced by ``over``.
    """
    out = deepcopy(base)
    for key, value in over.items():
        here = f"{path}.{key}" if path else key
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value, here)
        else:
            out[key] = deepcopy(value)
    return out


def _load_yaml(text: str) -> dict:
    """Parse a YAML config document.

    Args:
        text: Raw YAML source.

    Returns:
        The parsed mapping; an empty dict for empty input.

    Raises:
        TypeError: If the document's root is not a mapping.
    """
    data = yaml.load(text, Loader=_LOADER) or {}  # noqa: S506 - _LOADER is a *safe* loader
    if not isinstance(data, dict):
        raise TypeError("config root must be a mapping")
    return data


def _shipped_defaults() -> dict:
    """Read the packaged ``defaults.yml`` (phase map, matcher registries, noise patterns).

    Returns:
        The baseline config layer, before any user input.
    """
    text = resources.files("ci_doctor.config").joinpath("defaults.yml").read_text()
    return _load_yaml(text)


def default_config() -> Config:
    """Build the config as shipped, ignoring the repo file, env and CLI flags.

    This is the baseline ``ci-doctor config --diff`` compares against.

    Returns:
        The validated schema-defaults + ``defaults.yml`` config.
    """
    return Config(**_shipped_defaults())


def _coerce(raw: str) -> Any:  # noqa: ANN401 - a YAML scalar is genuinely any type
    """Parse an env var's string value into a YAML scalar.

    Args:
        raw: The raw environment value.

    Returns:
        The parsed value so ``true``, ``0.7`` and ``[a, b]` arrive typed; the
        original string if it is not valid YAML.
    """
    # Env values are strings; parse via YAML so `true`, `0.7`, `[a, b]` work.
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _env_overlay(environ: Mapping[str, str]) -> dict:
    """CI_DOCTOR_LLM__MODEL=x -> {"llm": {"model": "x"}}. "__" nests.

    Only vars whose first segment is a known top-level config field are consumed.
    That deliberately skips secret env vars like CI_DOCTOR_GITLAB_TOKEN (referenced
    by *name* via `token_env`, not a config key) so their presence never turns into
    an extra-key validation error.
    """
    out: dict = {}
    for key, value in environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        path = key[len(_ENV_PREFIX) :].lower().split("__")
        if path[0] not in _TOP_LEVEL:
            continue
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(value)
    return out


def load_config(
    repo_config: str | Path | Iterable[str | Path] | None = None,
    overrides: dict | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Assemble and validate the effective config from every layer.

    Layers apply lowest to highest: schema defaults, ``defaults.yml``, the repo
    config file(s), ``CI_DOCTOR_*`` env vars, then ``overrides``.

    Args:
        repo_config: One config file path, or several applied left to right so the
            last one wins. Defaults to ``./.ci-doctor.yml`` when that exists and
            none were given; no repo layer at all when it does not.
        overrides: The CLI-flag layer, applied last and therefore winning.
        environ: Environment to read. Defaults to :data:`os.environ`; tests pass
            ``{}`` to get a hermetic load.

    Returns:
        The validated config.

    Raises:
        FileNotFoundError: If a given path does not exist.
        pydantic.ValidationError: On an unknown or ill-typed key.
    """
    merged = _shipped_defaults()

    if isinstance(repo_config, (str, Path)):
        repo_config = [repo_config]
    paths = [Path(p) for p in repo_config or []]
    if not paths and _DEFAULT_REPO_CONFIG.is_file():
        paths = [_DEFAULT_REPO_CONFIG]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        merged = _deep_merge(merged, _load_yaml(path.read_text()))

    merged = _deep_merge(merged, _env_overlay(os.environ if environ is None else environ))
    if overrides:
        merged = _deep_merge(merged, overrides)

    return Config(**merged)  # extra="forbid" -> unknown keys raise ValidationError


def load_or_fail(paths: list[Path]) -> Config:
    """:func:`load_config` for a command, turning a bad config into a `SetupError`.

    Every command reads its config through this, so `analyze` and `matchers …`
    see the same layers, env vars included.

    Args:
        paths: The `--config/-f` files, lowest precedence first.

    Returns:
        The effective config.

    Raises:
        SetupError: If a file is missing or unreadable, or the result doesn't validate.
    """
    try:
        return load_config(repo_config=paths)
    except Exception as exc:  # noqa: BLE001 - every way a config can be wrong is the user's to fix
        raise SetupError(f"invalid config: {exc}") from None
