"""Registry transports beyond http(s) and local dirs: `git+https/ssh://` and `s3://`.

Neither one adds auth of its own: git uses whatever git already has (SSH agent,
credential helpers, `insteadOf`), S3 uses the standard AWS chain. Failures raise a
bare `SetupError(cause)`; `registry.py` wraps it with the registry name and location.
"""

import atexit
import functools
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from ci_doctor.errors import SetupError
from ci_doctor.matchers.validate import contained

#: The only transports git may use; everything else (`ext::`, `file`, `git`) is refused.
_PROTOCOLS = ("https", "ssh")
GIT_TIMEOUT = 60.0
#: Per-registry checkout ceiling.
# ponytail: checked after checkout; partial clone + sparse checkout if registries outgrow it.
MAX_CHECKOUT_BYTES = 100 * 1024 * 1024
SCP_STYLE = re.compile(r"^[\w.-]+@[\w.-]+:(?!//)")
_checkouts: dict[str, Path] = {}


def is_git(location: str) -> bool:
    """Whether a location is a `git+` registry."""
    return location.startswith("git+")


def check_git(value: str) -> None:
    """Validate a `git+https://` / `git+ssh://` location without touching the network.

    Args:
        value: As written under `matchers.registries`.

    Raises:
        SetupError: On scp-style syntax, a transport git may not use, or no host.
    """
    if SCP_STYLE.match(value):
        raise SetupError("scp-style git locations are not accepted: write it as a git+ssh:// URL")
    _parse_git(value)


def _parse_git(value: str) -> tuple[str, str, str]:
    """Split `git+https://host/repo.git[@ref][#subdirectory=dir]` into url, ref, subdirectory."""
    parts = urlsplit(value.removeprefix("git+"))
    if parts.scheme not in _PROTOCOLS:
        allowed = ", ".join(f"git+{p}://" for p in _PROTOCOLS)
        raise SetupError(f"unsupported git transport {parts.scheme!r}: use {allowed}")
    if not parts.hostname and parts.scheme != "file":
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
    for proto in _PROTOCOLS:
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


@functools.cache
def _s3_client() -> Any:  # noqa: ANN401 - boto3 is an optional extra, so no type to name
    """The run's S3 client, from the standard AWS credential/region/endpoint chain."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise SetupError("s3:// registries need `pip install ci-doctorr[s3]`") from None
    return boto3.client("s3", config=Config(connect_timeout=10, read_timeout=10, retries={"max_attempts": 2}))


def s3_get(location: str, rel: str, cap: int) -> bytes:
    """Read one object of an `s3://bucket/prefix/` registry, at most `cap` bytes.

    Args:
        location: The registry, ending in `/`.
        rel: The file, relative to the prefix.
        cap: The most bytes accepted.

    Returns:
        The object's bytes.

    Raises:
        SetupError: If the extra is missing, the object can't be read, or is over `cap`.
    """
    client = _s3_client()
    from botocore.exceptions import BotoCoreError, ClientError

    parts = urlsplit(location)
    key = parts.path.lstrip("/") + rel
    try:
        body = client.get_object(Bucket=parts.netloc, Key=key)["Body"].read(cap + 1)
    except ClientError as err:
        raise SetupError(f"GET {rel}: {err.response.get('Error', {}).get('Code', 'error')}") from None
    except BotoCoreError as err:
        raise SetupError(f"GET {rel}: {err}") from None
    if len(body) > cap:
        raise SetupError(f"{rel} is over the {cap}-byte cap")
    return body
