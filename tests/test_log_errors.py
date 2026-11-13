"""How a failed log download is described: a status and a hint, never the exception's text."""

from types import SimpleNamespace

import pytest
import requests

from ci_doctor.providers.log_errors import describe, retry_after

_SIGNED = "https://results-receiver.example/logs/1?sig=SECRET&se=2026"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (SimpleNamespace(status=401), "HTTP 401: not authenticated: set an API token with read access"),
        (
            SimpleNamespace(response_code=404),
            "HTTP 404: not found: the log has expired, or the token cannot see this repository",
        ),
        (
            requests.HTTPError(_SIGNED, response=SimpleNamespace(status_code=410)),
            "HTTP 410: gone: the log has expired",
        ),
        (SimpleNamespace(status=418), "HTTP 418: the CI API refused the request"),
        (SimpleNamespace(status=502), "HTTP 502: the CI API failed"),
    ],
)
def test_a_status_code_becomes_an_actionable_line(exc, expected):
    """Each SDK's status attribute is read, whichever SDK raised."""
    assert describe(exc) == expected  # ty: ignore[invalid-argument-type]


def test_no_status_names_the_exception_type_only():
    """A timeout has no status: its type says enough, and its message is never used."""
    assert describe(requests.ConnectionError(_SIGNED)) == "log download failed (ConnectionError)"


def test_the_signed_log_url_never_reaches_the_description():
    """GitHub's log URL is signed; an HTTP error's message carries it, query string and all."""
    exc = requests.HTTPError(
        f"404 Client Error for url: {_SIGNED}", response=SimpleNamespace(status_code=404)
    )
    assert "SECRET" not in describe(exc)


@pytest.mark.parametrize(
    ("status", "headers", "wait"),
    [
        (429, {}, 1.0),
        (429, {"Retry-After": "3"}, 3.0),
        (403, {"Retry-After": "0"}, 0.0),
        (403, {}, None),  # a 403 without Retry-After is a permission problem, not a rate limit
        (429, {"Retry-After": "11"}, None),  # past the cap
        (429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
        (500, {"Retry-After": "1"}, None),
    ],
)
def test_only_a_short_rate_limit_is_worth_one_retry(status, headers, wait):
    """Retry a rate limit the server says will pass soon; report everything else."""
    assert retry_after(status, headers) == wait
