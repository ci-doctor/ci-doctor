"""The HTTP client every provider uses to call its REST API.

A provider needs only a few calls (the run, its jobs, a job's log, PR/MR comments), so
it uses this client instead of a vendor SDK. The client handles auth, TLS, pagination,
rate limits and redirects; a provider only names endpoints and maps the JSON.

Redirects are followed here, not by httpx: httpx strips `Authorization` on a redirect
to another host but not GitLab's `PRIVATE-TOKEN`. A hop to another host gets no
credentials. Rate-limit waits are capped, so a long limit fails instead of hanging.
"""

import logging
import ssl
import time
from collections.abc import Callable, Iterator, Mapping
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ci_doctor import __version__
from ci_doctor.config.schemas._api import ApiLimits

log = logging.getLogger("ci_doctor.providers")

#: GitHub's log download needs one redirect. Fixed, not a setting: each hop is a chance
#: to send a request somewhere unexpected.
MAX_REDIRECTS = 5
#: GitHub's advice when a secondary rate limit has no `Retry-After`.
_SECONDARY_WAIT_SECONDS = 60.0
_ERROR_MESSAGE_CHARS = 200
_DEFAULT_PORTS = {"http": 80, "https": 443}


class ApiError(Exception):
    """A provider's API returned an error, or did not respond.

    Attributes:
        vendor: Whose API.
        status: The HTTP status, or None when there was no response.
        detail: The status and the API's message. Never the URL: it can be a signed one.
    """

    def __init__(self, vendor: str, status: int | None, message: str = "") -> None:
        """Build the error.

        Args:
            vendor: Whose API, e.g. "GitHub".
            status: The HTTP status, or None.
            message: What the API said, or the error type when there was no response.
        """
        if status is None:
            self.detail = message or "no response"
        else:
            message = message.removeprefix(f"{status} ")  # GitLab says "404 Project Not Found"
            self.detail = f"HTTP {status}: {message}" if message else f"HTTP {status}"
        self.vendor = vendor
        self.status = status
        super().__init__(f"{vendor} API: {self.detail}")


def tls_verify(ca_bundle: str | None, verify_ssl: bool, environ: Mapping[str, str]) -> ssl.SSLContext | bool:
    """The `verify=` value for a provider's endpoint.

    httpx ignores `REQUESTS_CA_BUNDLE`, which the docs say is honoured, so it is read
    here. httpx reads `SSL_CERT_FILE` and `SSL_CERT_DIR` itself.

    Args:
        ca_bundle: `<vendor>.ca_bundle` from the config.
        verify_ssl: `<vendor>.verify_ssl` from the config.
        environ: The environment to read the CA variables from.

    Returns:
        False, True (system roots), or a context trusting the bundle file or directory.

    Raises:
        ValueError: If the CA bundle cannot be read.
    """
    if ca_bundle:
        path = ca_bundle
    elif not verify_ssl:
        return False
    else:
        path = environ.get("REQUESTS_CA_BUNDLE") or environ.get("CURL_CA_BUNDLE") or ""
        if not path:
            return True
    try:
        if Path(path).is_dir():
            return ssl.create_default_context(capath=path)
        return ssl.create_default_context(cafile=path)
    except (OSError, ssl.SSLError) as err:
        raise ValueError(f"CA bundle {path} cannot be read: {err}") from None


class ApiClient:
    """A REST client: JSON calls, pagination, rate-limit waits, and a token that stays on its host."""

    def __init__(
        self,
        vendor: str,
        base_url: str,
        *,
        auth: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        verify: ssl.SSLContext | bool = True,
        timeout: float = 30,
        limits: ApiLimits | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Set up the client. Nothing is sent until the first call.

        Args:
            vendor: Whose API, for error messages.
            base_url: The API root; call paths are appended to it.
            auth: Credential headers, sent only to the API's host. Empty for no token.
            headers: Other headers for the API's host, such as `Accept`.
            verify: TLS verification, from :func:`tls_verify`.
            timeout: Seconds per request.
            limits: Pagination and rate-limit caps, from `<vendor>.limits`. Defaults to the defaults.
            transport: Replaces the network, for tests.
            sleep: Waits out a rate limit; tests pass one that records instead.
            clock: Current epoch seconds, to turn a reset time into a wait.
        """
        self.vendor = vendor
        self._base = base_url.rstrip("/")
        self._origin = _origin(self._base)
        self._auth = dict(auth)
        self._headers = {"User-Agent": f"ci-doctor/{__version__}", **(headers or {})}
        self._limits = limits or ApiLimits()
        self._sleep = sleep
        self._clock = clock
        self._http = httpx.Client(verify=verify, timeout=timeout, transport=transport)

    # --- calls ------------------------------------------------------------

    def get_json(self, path: str, **params: str | int) -> Any:  # noqa: ANN401 - whatever the API returned
        """GET a JSON document.

        Args:
            path: Under the base URL, e.g. `/repos/acme/app/actions/runs/1`.
            **params: Query parameters.

        Returns:
            The decoded body.

        Raises:
            ApiError: On an error status, a network failure, a body that is not JSON,
                or a rate limit past the cap.
        """
        return self._json(self.request("GET", path, params=params or None))

    def send_json(self, method: str, path: str, body: Mapping[str, Any]) -> Any:  # noqa: ANN401 - whatever the API returned
        """POST, PUT or PATCH a JSON body.

        Args:
            method: The HTTP method.
            path: Under the base URL.
            body: The JSON to send.

        Returns:
            The decoded reply.

        Raises:
            ApiError: As for :meth:`get_json`.
        """
        return self._json(self.request(method, path, json=body))

    def get_text(self, path: str) -> str:
        """GET a text body, such as a job's log.

        Args:
            path: Under the base URL.

        Returns:
            The body as UTF-8. An invalid byte becomes U+FFFD instead of failing the log.

        Raises:
            ApiError: As for :meth:`get_json`.
        """
        return self.request("GET", path).content.decode("utf-8", "replace")

    def paginate(self, path: str, *, key: str | None = None, **params: str | int) -> Iterator[dict[str, Any]]:
        """Every item of a paged list, following `Link: rel="next"` or `x-next-page`.

        Args:
            path: Under the base URL.
            key: The field holding the items (GitHub's `jobs`), or None if the body is the list.
            **params: Query parameters for the first page.

        Yields:
            The items, in API order.

        Raises:
            ApiError: As for :meth:`get_json`, or past `limits.max_pages`. A cut list would hide jobs.
        """
        first: dict[str, str | int] = {"per_page": 100, **params}
        url, query = path, first
        for _ in range(self._limits.max_pages):
            resp = self.request("GET", url, params=query)
            body = self._json(resp)
            yield from body[key] if key else body
            if next_url := resp.links.get("next", {}).get("url"):
                url, query = next_url, None
            elif next_page := resp.headers.get("x-next-page"):
                url, query = path, {**first, "page": next_page}
            else:
                return
        raise ApiError(self.vendor, None, f"more than {self._limits.max_pages} pages")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Send a request, waiting out a short rate limit.

        Args:
            method: The HTTP method.
            path: Under the base URL, or an absolute URL from a `Link` header.
            params: Query parameters.
            json: A JSON body.

        Returns:
            The successful response.

        Raises:
            ApiError: On an error status, or a rate limit past `limits.rate_limit_max_wait_seconds`
                or `limits.rate_limit_retries`.
        """
        url = path if path.startswith(("http://", "https://")) else self._base + path
        attempts = self._limits.rate_limit_retries + 1
        for attempt in range(1, attempts + 1):
            resp = self._send(method, url, params, json)
            if resp.is_success:
                return resp
            wait = self._rate_limit_wait(resp, attempt)
            if wait is None:
                raise ApiError(self.vendor, resp.status_code, _message(resp))
            if wait > self._limits.rate_limit_max_wait_seconds or attempt == attempts:
                raise ApiError(self.vendor, resp.status_code, f"rate limited; retry in {wait:.0f}s")
            log.warning(f"{self.vendor} rate limit: waiting {wait:.0f}s (attempt {attempt}/{attempts})")
            self._sleep(wait)
        raise AssertionError("unreachable: the last attempt returns or raises")  # pragma: no cover

    # --- internals --------------------------------------------------------

    def _send(
        self,
        method: str,
        url: str,
        params: Mapping[str, str | int] | None,
        json: Mapping[str, Any] | None,
    ) -> httpx.Response:
        """Send one request and follow its redirects.

        Args:
            method: The HTTP method.
            url: An absolute URL.
            params: Query parameters, for the first hop only.
            json: A JSON body, for the first hop only.

        Returns:
            The first response that is not a redirect.

        Raises:
            ApiError: On a network failure, a redirect from https to http, or too many redirects.
        """
        for _ in range(MAX_REDIRECTS + 1):
            try:
                resp = self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=self._headers_for(url),
                    follow_redirects=False,
                )
            except httpx.HTTPError as err:
                raise ApiError(self.vendor, None, type(err).__name__) from None
            if not resp.has_redirect_location:
                return resp
            target = urljoin(url, resp.headers["location"])
            if url.startswith("https:") and target.startswith("http:"):
                raise ApiError(self.vendor, None, "refused a redirect from https to http")
            if resp.status_code in (301, 302, 303):
                method, json = "GET", None  # standard; 307 and 308 keep the method
            url, params = target, None
        raise ApiError(self.vendor, None, f"more than {MAX_REDIRECTS} redirects")

    def _headers_for(self, url: str) -> dict[str, str]:
        """All headers for the API's host; only the user agent for any other host.

        Args:
            url: Where the request goes.

        Returns:
            The headers to send.
        """
        if _origin(url) == self._origin:
            return {**self._headers, **self._auth}
        return {"User-Agent": self._headers["User-Agent"]}

    def _rate_limit_wait(self, resp: httpx.Response, attempt: int) -> float | None:
        """Seconds to wait before retrying, if the response is a rate limit.

        GitHub signals a limit with a 403 or 429 and one of: `Retry-After`, an
        exhausted `x-ratelimit-remaining`, or only the words "rate limit" in the body.

        Args:
            resp: A response that was not a success.
            attempt: The attempt number, from 1.

        Returns:
            Seconds to wait, or None if this is not a rate limit.
        """
        if resp.status_code not in (403, 429):
            return None
        headers = resp.headers
        if (retry_after := headers.get("retry-after")) is not None:
            return self._seconds_until(retry_after)
        reset = headers.get("x-ratelimit-reset", "")
        if headers.get("x-ratelimit-remaining") == "0" and reset.isdigit():
            return max(0.0, int(reset) - self._clock()) + 1
        if resp.status_code == 429:
            return float(2**attempt)
        if "rate limit" in _message(resp).lower():
            return _SECONDARY_WAIT_SECONDS
        return None

    def _seconds_until(self, retry_after: str) -> float:
        """Convert `Retry-After` (seconds or an HTTP date) to seconds.

        Args:
            retry_after: The header's value.

        Returns:
            Seconds to wait, at least 0; 1 if the value cannot be read.
        """
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
        try:
            return max(0.0, parsedate_to_datetime(retry_after).timestamp() - self._clock())
        except (TypeError, ValueError):
            return 1.0

    def _json(self, resp: httpx.Response) -> Any:  # noqa: ANN401 - whatever the API returned
        """Decode a JSON body.

        Args:
            resp: A successful response.

        Returns:
            The decoded JSON.

        Raises:
            ApiError: If the body is not JSON.
        """
        try:
            return resp.json()
        except ValueError:
            raise ApiError(self.vendor, resp.status_code, "the reply was not JSON") from None


def _origin(url: str) -> tuple[str, str, int | None]:
    """The scheme, host and port of a URL, the default port filled in.

    Args:
        url: An absolute URL.

    Returns:
        The triple. Two URLs with the same triple may share a credential.
    """
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(parts.scheme)


def _message(resp: httpx.Response) -> str:
    """The error message from a response, on one short line.

    Args:
        resp: The response.

    Returns:
        The API's `message` or `error` field, else the status's reason phrase. Never the URL.
    """
    try:
        body = resp.json()
    except ValueError:
        body = None
    said = (body.get("message") or body.get("error")) if isinstance(body, dict) else None
    return " ".join(str(said or resp.reason_phrase).split())[:_ERROR_MESSAGE_CHARS]
