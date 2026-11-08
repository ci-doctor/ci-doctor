"""What `ci-doctor report` shows.

Pasted into public issues, so nothing secret: variables by name only, files by
basename, `api_base` as set/unset.
"""

import os
import platform
import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from ci_doctor import __version__
from ci_doctor.config.loader import load_config, user_config
from ci_doctor.errors import SetupError

_DIST = "ci-doctorr"
_ENV = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "AWS_CA_BUNDLE",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "NO_COLOR",
    "PAGER",
)
_NAME = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Section:
    """One block of the report: a title and `(key, value)` lines.

    Attributes:
        title: Its heading.
        fields: `(key, value)` pairs, in display order.
    """

    title: str
    fields: list[tuple[str, str]]


def build(paths: list[Path]) -> list[Section]:
    """The report's sections for the given `-f` files.

    Args:
        paths: The `-f` files, as `analyze` would read them.

    Returns:
        The sections: ci-doctor and Python, packages, config, environment.
    """
    return [_runtime(), _packages(), _config(paths), _environment()]


def _runtime() -> Section:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        install = "container"
    elif sys.prefix != sys.base_prefix:
        install = "virtualenv"
    else:
        install = "system"
    return Section(
        "ci-doctor",
        [
            ("version", __version__),
            ("python", f"{platform.python_implementation()} {platform.python_version()} ({install})"),
            ("os", f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ],
    )


def _packages() -> Section:
    """Dependency and LLM SDK versions, both read from installed metadata."""
    requires = metadata.requires(_DIST) or []
    deps = [_pinned(name) for r in requires if "extra ==" not in r and (name := _name(r))]
    extras = metadata.metadata(_DIST).get_all("Provides-Extra") or []
    gated = [
        r for r in metadata.requires("pydantic-ai-slim") or [] if any(f"extra == '{e}'" in r for e in extras)
    ]
    sdks = sorted({_pinned(n) for r in gated if _version(n := _name(r))})
    return Section("packages", [("dependencies", ", ".join(deps)), ("llm sdks", ", ".join(sdks) or "none")])


def _config(paths: list[Path]) -> Section:
    files = ["user config"] if user_config().is_file() else []
    fields = [("files", ", ".join([*files, *(p.name for p in paths)]) or "none")]
    try:
        cfg = load_config(paths)
    except SetupError as err:
        return Section("config", [*fields, ("error", str(err))])
    llm, matchers = cfg.llm, cfg.matchers
    if llm.enabled:
        api_base = ", api_base set" if llm.api_base else ""
        llm_line = f"{llm.backend}, model {llm.model or 'unset'}{api_base}"
    else:
        llm_line = "disabled"
    return Section(
        "config",
        [
            *fields,
            ("ci / scm", f"{cfg.ci} / {cfg.scm_vendor}"),
            ("llm", llm_line),
            ("matchers", f"{len(matchers.include)} included, from {', '.join(sorted(matchers.registries))}"),
        ],
    )


def _environment() -> Section:
    if os.environ.get("GITLAB_CI"):
        ci = "gitlab"
    elif os.environ.get("GITHUB_ACTIONS"):
        ci = "github"
    else:
        ci = "other" if os.environ.get("CI") else "none"
    named = [n for n in _ENV if os.environ.get(n) or os.environ.get(n.lower())]
    named += sorted(k for k in os.environ if k.startswith("CI_DOCTOR_"))
    return Section("environment", [("ci", ci), ("set", ", ".join(named) or "none")])


def _name(requirement: str) -> str:
    match = _NAME.match(requirement)
    return match.group() if match else ""


def _version(dist: str) -> str | None:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def _pinned(dist: str) -> str:
    return f"{dist} {_version(dist) or 'missing'}"
