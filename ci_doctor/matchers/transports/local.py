"""A registry on disk: a path, or a `file://` URL."""

from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from ci_doctor.errors import SetupError
from ci_doctor.matchers.transports import Fetched, Transport, Validators
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
        """Read a file from a registry on disk, contained by its real path.

        Args:
            alias: Unused; a local read has nothing to warn about.
            location: The directory.
            rel: The file, relative to it.
            cap: The most bytes accepted.
            client: Unused.
            since: Unused; a disk read is never cached.

        Returns:
            The file.

        Raises:
            SetupError: If it can't be read, is over `cap`, or resolves outside the registry.
        """
        path = contained(Path(location), rel)
        if path.exists() and not path.is_file():
            raise SetupError(f"{rel} is not a regular file")  # a FIFO would block the read forever
        try:
            with path.open("rb") as f:
                body = f.read(cap + 1)  # bounded even if the file grows while it is read
        except OSError as err:
            raise SetupError(f"{rel}: {err.strerror or err}") from None
        if len(body) > cap:
            raise SetupError(f"{rel} is over the {cap}-byte cap")
        return Fetched(body)
