"""How a failed log download is described: a status and a hint, never the exception's text."""

from types import SimpleNamespace

import pytest
import requests

from ci_doctor.providers.log_errors import describe

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
