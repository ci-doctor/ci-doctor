"""A registry in a git repository: `git+https://` or `git+ssh://`, checked out once per run.

No auth of its own: git uses whatever git already has (SSH agent, credential
helpers, `insteadOf`).
"""

import atexit
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx

from ci_doctor.errors import SetupError
from ci_doctor.matchers.transports import SCP_STYLE, Transport
from ci_doctor.matchers.transports.local import LocalTransport
from ci_doctor.matchers.validate import contained

#: The only transports git may use; everything else (`ext::`, `file`, `git`) is refused.
_PROTOCOLS = ("https", "ssh")
#: Test seam: the tests clone a local bare repository over `file://`, which has no host.
#: Never set outside tests.
ALLOW_FILE = False
GIT_TIMEOUT = 60.0
#: Per-registry checkout ceiling.
# ponytail: checked after checkout; partial clone + sparse checkout if registries outgrow it.
MAX_CHECKOUT_BYTES = 100 * 1024 * 1024
_checkouts: dict[str, Path] = {}


def _protocols() -> tuple[str, ...]:
    """The transports git may use, widened to `file` only under the test seam."""
    return (*_PROTOCOLS, "file") if ALLOW_FILE else _PROTOCOLS


def _parse_git(value: str) -> tuple[str, str, str]:
    """Split `git+https://host/repo.git[@ref][#subdirectory=dir]` into url, ref, subdirectory."""
    parts = urlsplit(value.removeprefix("git+"))
    if parts.scheme not in _protocols():
        allowed = ", ".join(f"git+{p}://" for p in _PROTOCOLS)
        raise SetupError(f"unsupported git transport {parts.scheme!r}: use {allowed}")
    if not parts.hostname and not (ALLOW_FILE and parts.scheme == "file"):
        raise SetupError("git location has no host")
    path, _, ref = parts.path.partition("@")
    subdir = ""
    if parts.fragment:
        key, _, subdir = parts.fragment.partition("=")
        if key != "subdirectory" or not subdir:
            raise SetupError(f"unsupported fragment #{parts.fragment}: only #subdirectory=<dir>")
    url = urlunsplit(SplitResult(parts.scheme, parts.netloc, path, "", ""))
    return url, ref or "HEAD", subdir


def _dir_size(root: Path) -> int:
    """Bytes under `root`, ignoring `.git`."""
    total = 0
    for here, dirs, files in os.walk(root):
        if here == str(root):
            dirs[:] = [d for d in dirs if d != ".git"]
        total += sum((Path(here) / f).lstat().st_size for f in files)
    return total


def _git(args: list[str], cwd: Path, deadline: float) -> None:
    """Run one hardened git command within the checkout's remaining time."""
    flags = ["-c", "protocol.allow=never", "-c", "submodule.recurse=false"]
    for proto in _protocols():
        flags += ["-c", f"protocol.{proto}.allow=always"]
    # An inherited GIT_DIR (set inside git hooks) would aim init/fetch at the wrong repository.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env |= {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        done = subprocess.run(  # noqa: S603 - args are ours; the URL sits after `--`
            ["git", *flags, *args],  # noqa: S607
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=max(deadline - time.monotonic(), 0.1),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SetupError(f"git {args[0]} timed out (the whole checkout gets {GIT_TIMEOUT:.0f}s)") from None
    if done.returncode:
        raise SetupError(f"git {args[0]} failed: {done.stderr.strip() or done.returncode}")


def git_checkout(location: str) -> Path:
    """Check a `git+` registry out once per run and return its directory.

    Args:
        location: A `git+https://` or `git+ssh://` location.

    Returns:
        The checkout, or its `#subdirectory=`; removed at exit.

    Raises:
        SetupError: If git is missing, the fetch fails or times out, the checkout
            is over the size ceiling, or the subdirectory is absent or escapes.
    """
    if location in _checkouts:
        return _checkouts[location]
    if not shutil.which("git"):
        raise SetupError("git+ registries need git on PATH")
    url, ref, subdir = _parse_git(location)
    tmp = Path(tempfile.mkdtemp(prefix="ci-doctor-registry-"))
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    deadline = time.monotonic() + GIT_TIMEOUT
    try:
        _git(["init", "-q"], tmp, deadline)
        _git(["fetch", "-q", "--depth", "1", "--no-recurse-submodules", "--", url, ref], tmp, deadline)
        _git(["checkout", "-q", "FETCH_HEAD"], tmp, deadline)
        size = _dir_size(tmp)
        if size > MAX_CHECKOUT_BYTES:
            raise SetupError(f"checkout is {size} bytes, over the {MAX_CHECKOUT_BYTES}-byte cap")
        root = contained(tmp, subdir) if subdir else tmp
        if not root.is_dir():
            raise SetupError(f"subdirectory {subdir!r} is not in the repository")
    except SetupError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _checkouts[location] = root
    return root


class GitTransport(Transport):
    """Check the repository out, then read it like a local registry."""

    def locate(self, value: str) -> str:
        """Validate a `git+https://` / `git+ssh://` location without touching the network.

        Args:
            value: As written under `matchers.registries`.

        Returns:
            The location, unchanged: the checkout happens on the first read.

        Raises:
            SetupError: On scp-style syntax, a transport git may not use, or no host.
        """
        if SCP_STYLE.match(value):
            raise SetupError("scp-style git locations are not accepted: write it as a git+ssh:// URL")
        _parse_git(value)
        return value

    def read(self, alias: str, location: str, rel: str, *, cap: int, client: httpx.Client) -> str:
        """Read one file from the run's checkout of the repository.

        Args:
            alias: The registry's alias.
            location: The `git+` location.
            rel: The file, relative to the registry directory.
            cap: The most bytes accepted.
            client: Unused; git does its own fetching.

        Returns:
            The file's text.

        Raises:
            SetupError: If the checkout fails, or the file can't be read from it.
        """
        root = git_checkout(location)
        return LocalTransport().read(alias, str(root), rel, cap=cap, client=client)
