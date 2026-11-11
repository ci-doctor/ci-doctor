"""How a registry's files are read: one transport per kind of location, picked by its shape.

The same shape as `providers/registry.py`: an interface, one module per transport,
and a table mapping a name to a dotted path imported on demand. The table holds
paths rather than classes so this module never imports its own submodules, which
import the interface from here.

A transport raises a bare `SetupError(cause)`; `registry.py` wraps it once with the
registry's alias and location, so every loading error has the same shape.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from importlib import import_module
from typing import ClassVar
from urllib.parse import urlsplit

import httpx

from ci_doctor.errors import SetupError

#: Transport name -> its class.
TRANSPORTS: dict[str, str] = {
    "local": "ci_doctor.matchers.transports.local:LocalTransport",
    "http": "ci_doctor.matchers.transports.http:HttpTransport",
}

#: Possessive, so a long run with no `@` after it fails once instead of backtracking.
_USERINFO = re.compile(r"://[^/@\s]++@")


@dataclass(frozen=True)
class Validators:
    """What a cached response is revalidated with (RFC 9110 conditional requests).

    Attributes:
        etag: The response's `ETag`, sent back as `If-None-Match`.
        last_modified: Its `Last-Modified`, sent back as `If-Modified-Since` when there is no ETag.
    """

    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class Fetched:
    """One file as a transport read it.

    Attributes:
        body: Its bytes, or None when the server answered `304`: unchanged since `since`.
        validators: The response's validators, to revalidate it with next time.
        max_age: Seconds the response stays fresh (`Cache-Control: max-age` minus `Age`); 0 if unsaid.
    """

    body: bytes | None
    validators: Validators = field(default_factory=Validators)
    max_age: float = 0.0


class Transport(ABC):
    """Read files from one kind of registry location."""

    #: Whether the registry cache keeps what this transport reads.
    cacheable: ClassVar[bool] = False

    @abstractmethod
    def locate(self, value: str) -> str:
        """Normalise a configured location to the directory holding `index.yaml`.

        Args:
            value: As written under `matchers.registries`.

        Returns:
            The location every later :meth:`read` is given.

        Raises:
            SetupError: If this transport refuses the location.
        """

    @abstractmethod
    def read(
        self,
        alias: str,
        location: str,
        rel: str,
        *,
        cap: int,
        client: httpx.Client,
        since: Validators | None = None,
    ) -> Fetched:
        """Read one file of a registry, as the bytes its index hashes.

        Args:
            alias: The registry's alias, for warnings.
            location: Its directory, from :meth:`locate`.
            rel: The file, relative to that directory (already path-checked by `Index`).
            cap: The most bytes accepted.
            client: The run's shared HTTP client.
            since: A cached copy's validators: a transport that can ask "changed since?"
                may answer with `body=None`. Others ignore it.

        Returns:
            The file, or `body=None` if it is unchanged since `since`.

        Raises:
            SetupError: If it can't be read, is over `cap`, or leaves the registry.
        """


def redact(text: str) -> str:
    """Hide credentials embedded in a URL (`https://user:token@host`).

    Args:
        text: A message that may quote a registry location.

    Returns:
        The message with every URL's userinfo replaced by `***`.
    """
    return _USERINFO.sub("://***@", text)


def decode(rel: str, body: bytes) -> str:
    """UTF-8 text, or a `SetupError` naming the file.

    Args:
        rel: The file.
        body: Its bytes.

    Returns:
        The decoded text.

    Raises:
        SetupError: If the bytes are not UTF-8.
    """
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise SetupError(f"{rel} is not UTF-8 text") from None


def _kind(location: str) -> str:
    """Which transport serves a location.

    Args:
        location: A configured location, or one already normalised by a transport.

    Returns:
        A `TRANSPORTS` key.

    Raises:
        SetupError: On a scheme no transport serves.
    """
    scheme = urlsplit(location).scheme
    if scheme in ("http", "https"):
        return "http"
    if scheme == "file" or len(scheme) <= 1:  # one letter is a Windows drive, not a scheme
        return "local"
    raise SetupError(f"unsupported scheme {scheme!r}: use a path, file:// or http(s)://")


def transport_for(location: str) -> Transport:
    """Build the transport for a location.

    Args:
        location: A configured location, or one already normalised by a transport.

    Returns:
        A :class:`Transport`.

    Raises:
        SetupError: On a scheme no transport serves.
    """
    module, _, name = TRANSPORTS[_kind(location)].partition(":")
    return getattr(import_module(module), name)()
