"""A registry served over `http(s)://`: any static host (Pages, S3, a CDN, an internal server)."""

import logging
from urllib.parse import urljoin, urlsplit

import httpx

from ci_doctor.errors import SetupError
from ci_doctor.matchers.transports import Transport, decode, redact

log = logging.getLogger("ci_doctor.matchers")

MAX_REDIRECTS = 3


class HttpTransport(Transport):
    """GET a registry's files through the run's shared client."""

    def locate(self, value: str) -> str:
        """The URL as a directory, so relative paths join under it.

        Args:
            value: As written under `matchers.registries`.

        Returns:
            The URL, ending in `/`.
        """
        return value if value.endswith("/") else value + "/"

    def read(self, alias: str, location: str, rel: str, *, cap: int, client: httpx.Client) -> str:
        """GET a registry file: bounded redirects that never downgrade (another host warns), capped size.

        Args:
            alias: The registry's alias, for the cross-host warning.
            location: Its directory URL, ending in `/`.
            rel: The file, relative to it.
            cap: The most bytes accepted, checked while streaming.
            client: The run's shared HTTP client.

        Returns:
            The file's text.

        Raises:
            SetupError: On a refused redirect, a non-200 answer, an oversized body,
                or a transport error.
        """
        url = urljoin(location, rel)
        if not url.startswith(location):
            raise SetupError(f"{rel} resolves outside the registry")
        host = urlsplit(location).hostname
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", url, follow_redirects=False) as resp:
                    if not resp.is_redirect:
                        return _body(rel, resp, cap)
                    url = _follow(alias, rel, url, resp, host)
            except httpx.HTTPError as err:
                raise SetupError(f"GET {rel}: {str(err) or type(err).__name__}") from None
        raise SetupError(f"{rel}: more than {MAX_REDIRECTS} redirects")


def _follow(alias: str, rel: str, url: str, resp: httpx.Response, host: str | None) -> str:
    """The next URL of a redirect, refusing an https downgrade and warning on another host.

    Args:
        alias: The registry's alias, for the cross-host warning.
        rel: The file being read, for messages.
        url: The URL that answered with the redirect.
        resp: Its redirect response.
        host: The registry's own host.

    Returns:
        The absolute URL to GET next.

    Raises:
        SetupError: If the redirect leaves https.
    """
    nxt = urljoin(url, resp.headers.get("location", ""))
    if urlsplit(url).scheme == "https" and urlsplit(nxt).scheme != "https":
        raise SetupError(f"{rel}: redirect from https to {nxt} refused")
    if urlsplit(nxt).hostname != host:
        log.warning(f"registry {alias!r}: {rel} redirects to another host ({redact(nxt)})")
    return nxt


def _body(rel: str, resp: httpx.Response, cap: int) -> str:
    """A final response's text, streamed under the size cap.

    Args:
        rel: The file being read, for messages.
        resp: The non-redirect response.
        cap: The most bytes accepted.

    Returns:
        The decoded body.

    Raises:
        SetupError: On a non-200 answer or a body over `cap`.
    """
    if resp.status_code != 200:
        raise SetupError(f"GET {rel}: HTTP {resp.status_code}")
    body = bytearray()
    for chunk in resp.iter_bytes():
        body += chunk
        if len(body) > cap:
            raise SetupError(f"{rel} is over the {cap}-byte cap")
    return decode(rel, bytes(body))
