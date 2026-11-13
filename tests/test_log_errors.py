"""How a failed log download is described: a status and a hint, never the exception's text."""

from types import SimpleNamespace

import pytest
import requests

from ci_doctor.providers.log_errors import describe, retry_after

_SIGNED = "https://results-receiver.example/logs/1?sig=SECRET&se=2026"


@pytest.mark.parametrize(
    ("exc", "status", "says"),
    [
        (SimpleNamespace(status=401), 401, "token"),  # PyGithub
        (SimpleNamespace(response_code=404), 404, "expired"),  # python-gitlab
        (requests.HTTPError(_SIGNED, response=SimpleNamespace(status_code=410)), 410, "expired"),  # requests
        (SimpleNamespace(status=418), 418, "refused"),
        (SimpleNamespace(status=502), 502, "failed"),
    ],
)
def test_a_status_code_becomes_an_actionable_line(exc, status, says):
    """Each SDK's status attribute is read, whichever SDK raised, and the line says what to do."""
    line = describe(exc)  # ty: ignore[invalid-argument-type]
    assert line.startswith(f"HTTP {status}: ")
    assert says in line


def test_no_status_names_the_exception_type_only():
    """A timeout has no status: its type says enough, and its message is never used."""
    line = describe(requests.ConnectionError(_SIGNED))
    assert "ConnectionError" in line and "HTTP" not in line


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
