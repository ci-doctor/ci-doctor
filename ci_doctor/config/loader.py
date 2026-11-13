"""Layered config: schema defaults < defaults.yml < user file < repo file < env < flags.

Mappings deep-merge; lists and scalars replace, so a repo's `matchers.include` is the
whole selection. Unknown keys in a file are an error; env vars are laxer
(:func:`_env_overlay`).
"""

import logging
import os
from collections.abc import Iterable, Mapping
from copy import deepcopy
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click
import yaml

from ci_doctor.config.schemas import Config
from ci_doctor.errors import SetupError

log = logging.getLogger(__name__)

#: libyaml's safe loader when the wheel ships it (~10x faster than pure Python, and
#: `defaults.yml` is parsed on every run), else pure Python.
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_ENV_PREFIX = "CI_DOCTOR_"
_TOP_LEVEL = set(Config.model_fields)  # provider, gitlab, llm, ...
_DEFAULT_REPO_CONFIG = Path(".ci-doctor.yml")


def user_config() -> Path:
    """The user's config file (click's per-platform app dir), whether or not it exists.

    Returns:
        The path.
    """
    return Path(click.get_app_dir("ci-doctor")) / "config.yml"


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


def anchor_registry(value: Any, base: Path) -> Any:  # noqa: ANN401 - unvalidated config
    """Resolve a relative registry path against the directory of the file that names it.

    `local: ./reg` in `sub/.ci-doctor.yml` means `sub/reg`, wherever the command runs.
    Only a config file has a directory; a location from the env stays relative to the cwd.

    Args:
        value: A `matchers.registries` value as written, not yet validated.
        base: The config file's directory.

    Returns:
        `base / value` for a relative path; anything else (a URL, an absolute path, a
        non-string) unchanged.
    """
    if not isinstance(value, str) or not base.parts or urlsplit(value).scheme or Path(value).is_absolute():
        return value
    return str(base / value)


def _repo_paths(repo_config: str | Path | Iterable[str | Path] | None) -> list[Path]:
    """The repo config files to layer, lowest precedence first.

    Args:
        repo_config: As given to :func:`load_config`.

    Returns:
        The given files, or `./.ci-doctor.yml` when none were given and it exists.

    Raises:
        SetupError: If a given file does not exist.
    """
    if isinstance(repo_config, (str, Path)):
        repo_config = [repo_config]
    paths = [Path(p) for p in repo_config or []]
    if not paths:
        return [_DEFAULT_REPO_CONFIG] if _DEFAULT_REPO_CONFIG.is_file() else []
    if missing := [p for p in paths if not p.is_file()]:
        raise SetupError(f"config file not found: {missing[0]}")
    return paths


def _file_layer(path: Path) -> dict:
    """Read one config file, its relative registry paths anchored to its directory.

    Args:
        path: An existing config file.

    Returns:
        The file's layer, not yet validated.
    """
    layer = _load_yaml(path.read_text())
    registries = (layer.get("matchers") or {}).get("registries")
    if isinstance(registries, dict):  # anything else fails validation later
        for alias, value in registries.items():
            registries[alias] = anchor_registry(value, path.parent)
    return layer


#: What a repository's own config file may not set when the repository is not
#: trusted: where API calls and tokens go (`github`, `gitlab`), what a model is sent
#: and where (`llm`), and where matcher files are fetched from (`matchers.registries`).
#: An agent runs ci-doctor in whatever repository is open, so a hostile
#: `.ci-doctor.yml` could otherwise point the token at its own server, or name a
#: local file as `token_file` and receive its contents as the header.
UNTRUSTED_REPO_KEYS: tuple[tuple[str, ...], ...] = (
    ("github",),
    ("gitlab",),
    ("llm",),
    ("matchers", "registries"),
)


def load_config(
    repo_config: str | Path | Iterable[str | Path] | None = None,
    overrides: dict | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    """Assemble and validate the effective config from every layer.

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
        SetupError: If a file is missing, unreadable or not YAML, or the result
            doesn't validate (an unknown or ill-typed key).
    """
    return _assemble(repo_config, overrides, environ, trust_repo=True)[0]


def load_untrusted_repo_config(
    repo_config: str | Path | Iterable[str | Path] | None,
    overrides: dict | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Config, list[str]]:
    """Like :func:`load_config`, but without letting the repository choose where anything goes.

    For a caller that runs in a repository it did not choose: the keys in
    :data:`UNTRUSTED_REPO_KEYS` are taken from the user's own config, the
    environment and ``overrides`` only, and dropped from every repo file. The repo
    file still sets what is about the project itself: which matchers, the phases,
    analysis and redaction settings.

    Args:
        repo_config: The repository's config file(s), as for :func:`load_config`.
        overrides: The caller's own layer, applied last and trusted.
        environ: Environment to read. Defaults to :data:`os.environ`.

    Returns:
        The validated config, and the dotted keys dropped from the repo files, in
        file order, so the caller can say what it ignored.

    Raises:
        SetupError: As for :func:`load_config`.
    """
    return _assemble(repo_config, overrides, environ, trust_repo=False)


def _assemble(
    repo_config: str | Path | Iterable[str | Path] | None,
    overrides: dict | None,
    environ: Mapping[str, str] | None,
    *,
    trust_repo: bool,
) -> tuple[Config, list[str]]:
    """Merge and validate every layer; see :func:`load_config` for the order.

    Args:
        repo_config: The repo file(s).
        overrides: The last layer.
        environ: The environment, or None for :data:`os.environ`.
        trust_repo: False to drop :data:`UNTRUSTED_REPO_KEYS` from the repo files.

    Returns:
        The config, and the dotted keys dropped (always empty when trusted).

    Raises:
        SetupError: On any missing, unreadable or invalid layer.
    """
    layers = [_shipped_defaults()]
    dropped: list[str] = []
    try:
        user = [p for p in [user_config()] if p.is_file()]
        layers += [_file_layer(path) for path in user]
        for path in _repo_paths(repo_config):
            layer = _file_layer(path)
            if not trust_repo:
                dropped += _drop_untrusted(layer)
            layers.append(layer)
        layers.append(_env_overlay(os.environ if environ is None else environ))
        layers.append(overrides or {})
        merged: dict = {}
        for layer in layers:
            merged = _deep_merge(merged, layer)
        return Config(**merged), dropped  # extra="forbid" -> unknown keys raise ValidationError
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:  # ValidationError is a ValueError
        raise SetupError(f"invalid config: {exc}") from None


def _drop_untrusted(layer: dict) -> list[str]:
    """Remove :data:`UNTRUSTED_REPO_KEYS` from one repo layer, in place.

    Args:
        layer: A parsed repo file.

    Returns:
        The dotted keys it removed.
    """
    removed = []
    for path in UNTRUSTED_REPO_KEYS:
        parent = layer
        for key in path[:-1]:
            parent = parent.get(key) if isinstance(parent, dict) else None
        if isinstance(parent, dict) and path[-1] in parent:
            del parent[path[-1]]
            removed.append(".".join(path))
    return removed
