"""A registry served over `http(s)://`: any static host (Pages, S3, a CDN, an internal server)."""

import logging
import re
from urllib.parse import urljoin, urlsplit

import httpx

from ci_doctor.errors import SetupError
from ci_doctor.matchers.transports import Fetched, Transport, Unreachable, Validators, redact

log = logging.getLogger("ci_doctor.matchers")

MAX_REDIRECTS = 3
_MAX_AGE = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d+)")


class HttpTransport(Transport):
    """GET a registry's files through the run's shared client."""

    cacheable = True

    def locate(self, value: str) -> str:
        """The URL as a directory, so relative paths join under it.

        Args:
            value: As written under `matchers.registries`.

        Returns:
            The URL, ending in `/`.
        """
        return value if value.endswith("/") else value + "/"

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
        """GET a registry file: bounded redirects that never downgrade (another host warns), capped size.

        Args:
            alias: The registry's alias, for the cross-host warning.
            location: Its directory URL, ending in `/`.
            rel: The file, relative to it.
            cap: The most bytes accepted, checked while streaming.
            client: The run's shared HTTP client.
            since: A cached copy's validators, sent as `If-None-Match` / `If-Modified-Since`.

        Returns:
            The file with its validators and freshness, or `body=None` on a `304`.

        Raises:
            Unreachable: On a connection error, a timeout or a 5xx.
            SetupError: On a refused redirect, any other answer than 200 (or 304 to a
                conditional request), or an oversized body.
        """
        url = urljoin(location, rel)
        if not url.startswith(location):
            raise SetupError(f"{rel} resolves outside the registry")
        host = urlsplit(location).hostname
        headers = _conditional(since)
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", url, headers=headers, follow_redirects=False) as resp:
                    if not resp.has_redirect_location:  # a 304 is 3xx too, but no redirect
                        return _fetched(rel, resp, cap, since)
                    url = _follow(alias, rel, url, resp, host)
            except httpx.HTTPError as err:
                raise Unreachable(f"GET {rel}: {str(err) or type(err).__name__}") from None
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


def _conditional(since: Validators | None) -> dict[str, str]:
    """The request headers that ask "changed since this copy?".

    Args:
        since: A cached copy's validators, or None.

    Returns:
        `If-None-Match`, else `If-Modified-Since`, else nothing (an ETag is the stronger check).
    """
    if since and since.etag:
        return {"if-none-match": since.etag}
    if since and since.last_modified:
        return {"if-modified-since": since.last_modified}
    return {}


def _max_age(resp: httpx.Response) -> float:
    """How many more seconds the response stays fresh: `max-age` minus `Age` (RFC 9111).

    Args:
        resp: The response.

    Returns:
        Seconds, 0 when the server gives no `max-age` or says `no-cache` / `no-store`.
    """
    control = resp.headers.get("cache-control", "").lower()
    if "no-cache" in control or "no-store" in control or not (m := _MAX_AGE.search(control)):
        return 0.0
    try:
        age = float(resp.headers.get("age", "0"))
    except ValueError:
        age = 0.0
    return max(0.0, int(m.group(1)) - age)


def _fetched(rel: str, resp: httpx.Response, cap: int, since: Validators | None) -> Fetched:
    """A final response as a :class:`Fetched`, its body streamed under the size cap.

    Args:
        rel: The file being read, for messages.
        resp: The non-redirect response.
        cap: The most bytes accepted.
        since: The validators the request was conditional on, if any.

    Returns:
        The file, or `body=None` on a `304` to a conditional request.

    Raises:
        SetupError: On any other non-200 answer or a body over `cap`.
    """
    validators = Validators(etag=resp.headers.get("etag"), last_modified=resp.headers.get("last-modified"))
    if resp.status_code == 304 and since is not None:
        # A 304 may leave a validator out; the cached copy's still holds.
        kept = Validators(validators.etag or since.etag, validators.last_modified or since.last_modified)
        return Fetched(None, kept, _max_age(resp))
    return Fetched(_body(rel, resp, cap), validators, _max_age(resp))


def _body(rel: str, resp: httpx.Response, cap: int) -> bytes:
    """A final response's bytes, streamed under the size cap.

    Args:
        rel: The file being read, for messages.
        resp: The non-redirect response.
        cap: The most bytes accepted.

    Returns:
        The body.

    Raises:
        SetupError: On a non-200 answer or a body over `cap`.
    """
    if resp.status_code >= 500:
        raise Unreachable(f"GET {rel}: HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise SetupError(f"GET {rel}: HTTP {resp.status_code}")
    body = bytearray()
    for chunk in resp.iter_bytes():
        body += chunk
        if len(body) > cap:
            raise SetupError(f"{rel} is over the {cap}-byte cap")
    return bytes(body)
