"""A registry on disk: a path, or a `file://` URL."""

from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from ci_doctor.errors import SetupError
from ci_doctor.matchers.transports import Transport, decode
from ci_doctor.matchers.validate import contained


class LocalTransport(Transport):
    """Read a registry directory, every file contained by its real path."""

    def locate(self, value: str) -> str:
        """A path as written; a `file://` URL as its decoded path.

        Args:
            value: As written under `matchers.registries`.

        Returns:
            The directory's path.
        """
        parts = urlsplit(value)
        return unquote(parts.path) if parts.scheme == "file" else value

    def read(self, alias: str, location: str, rel: str, *, cap: int, client: httpx.Client) -> str:
        """Read a file from a registry on disk, contained by its real path.

        Args:
            alias: Unused; a local read has nothing to warn about.
            location: The directory.
            rel: The file, relative to it.
            cap: The most bytes accepted.
            client: Unused.

        Returns:
            The file's text.

        Raises:
            SetupError: If it can't be read, is over `cap`, or resolves outside the registry.
        """
        path = contained(Path(location), rel)
        try:
            size = path.stat().st_size
            body = path.read_bytes() if size <= cap else b""
        except OSError as err:
            raise SetupError(f"{rel}: {err.strerror or err}") from None
        if size > cap:
            raise SetupError(f"{rel} is {size} bytes, over the {cap}-byte cap")
        return decode(rel, body)
