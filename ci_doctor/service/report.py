"""What `ci-doctor report` shows: the facts a bug report needs, and nothing secret.

Pasted into a public issue, so every value is one a stranger may read. Environment
variables are reported as set or unset, never by value. Config files and endpoints
are named, not shown: a path carries a username, an `api_base` an internal host.
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
#: Variables that change how ci-doctor connects or prints, reported as set or unset.
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
    """One block of the report.

    Attributes:
        title: Its heading.
        fields: `(key, value)` pairs, in display order.
    """

    title: str
    fields: list[tuple[str, str]]


def build(paths: list[Path]) -> list[Section]:
    """Gather the report.

    Args:
        paths: The `-f` files, as `analyze` would read them.

    Returns:
        The sections: ci-doctor and Python, packages, config, environment.
    """
    return [_runtime(), _packages(), _config(paths), _environment()]


def _runtime() -> Section:
    container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    return Section(
        "ci-doctor",
        [
            ("version", __version__),
            ("python", f"{platform.python_implementation()} {platform.python_version()}"),
            ("os", f"{platform.system()} {platform.release()} ({platform.machine()})"),
            ("container", _yes(container)),
            ("virtualenv", _yes(sys.prefix != sys.base_prefix)),
        ],
    )


def _packages() -> Section:
    """Every direct dependency's version, then each installed LLM SDK's.

    Both come from the installed metadata: the dependencies from ours, the SDKs from
    what Pydantic AI adds for our backend extras, so neither list needs upkeep.
    """
    requires = metadata.requires(_DIST) or []
    fields = [(name, _version(name)) for r in requires if "extra ==" not in r and (name := _name(r))]
    extras = metadata.metadata(_DIST).get_all("Provides-Extra") or []
    gated = [
        r for r in metadata.requires("pydantic-ai-slim") or [] if any(f"extra == '{e}'" in r for e in extras)
    ]
    sdks = sorted({f"{n} {v}" for r in gated if (v := _version(n := _name(r))) != "not installed"})
    return Section("packages", [*fields, ("llm sdks", ", ".join(sdks) or "none")])


def _config(paths: list[Path]) -> Section:
    fields = [
        ("user config", "present" if user_config().is_file() else "absent"),
        ("-f files", ", ".join(p.name for p in paths) or "none"),
    ]
    try:
        cfg = load_config(paths)
    except SetupError as err:
        return Section("config", [*fields, ("error", str(err))])
    llm = cfg.llm
    return Section(
        "config",
        [
            *fields,
            ("ci", cfg.ci),
            ("scm", cfg.scm_vendor),
            ("llm", f"{llm.backend}, model {llm.model or 'unset'}" if llm.enabled else "disabled"),
            ("llm api_base", "set" if llm.api_base else "unset"),
            ("registries", ", ".join(sorted(cfg.matchers.registries)) or "none"),
            ("include", f"{len(cfg.matchers.include)} entries"),
        ],
    )


def _environment() -> Section:
    ci = "gitlab" if os.environ.get("GITLAB_CI") else "github" if os.environ.get("GITHUB_ACTIONS") else None
    own = sorted(k for k in os.environ if k.startswith("CI_DOCTOR_"))
    return Section(
        "environment",
        [
            ("ci", ci or ("other" if os.environ.get("CI") else "none")),
            *((name, _set(name)) for name in _ENV),
            ("CI_DOCTOR_*", ", ".join(own) or "none"),
            ("stdout encoding", sys.stdout.encoding or "unknown"),
        ],
    )


def _name(requirement: str) -> str:
    match = _NAME.match(requirement)
    return match.group() if match else ""


def _version(dist: str) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "not installed"


def _set(name: str) -> str:
    return "set" if os.environ.get(name) or os.environ.get(name.lower()) else "unset"


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"
