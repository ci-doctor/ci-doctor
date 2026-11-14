"""The providers' HTTP client: pagination, rate limits, redirects, errors and TLS.

Runs on `FakeApi`, so nothing touches the network, and `sleep` records waits instead of waiting.
"""

import ssl

import certifi
import httpx
import pytest

from ci_doctor.providers import api_client
from ci_doctor.providers.api_client import MAX_ATTEMPTS, ApiClient, ApiError, tls_verify
from tests.support import FakeApi, json_reply

BASE = "https://git.example.com/api/v4"
NOW = 1_700_000_000.0


def _client(routes, *, auth=None):
    """An ApiClient on a FakeApi. Returns the client, the API and the recorded waits."""
    api = FakeApi(routes)
    waits: list[float] = []
    client = ApiClient(
        "Test",
        BASE,
        auth={"PRIVATE-TOKEN": "tok"} if auth is None else auth,
        headers={"Accept": "application/json"},
        transport=api.transport,
        sleep=waits.append,
        clock=lambda: NOW,
    )
    return client, api, waits


# --- requests -----------------------------------------------------------------


def test_a_call_goes_to_the_base_url_with_the_token_and_headers():
    """The path is appended to the base URL, and the token and headers are sent."""
    client, api, _ = _client({("GET", "/api/v4/projects/1"): json_reply({"id": 1})})
    assert client.get_json("/projects/1") == {"id": 1}
    [sent] = api.requests
    assert sent.headers["private-token"] == "tok" and sent.headers["accept"] == "application/json"
    assert sent.headers["user-agent"].startswith("ci-doctor/")


def test_an_error_carries_the_status_and_the_apis_own_words():
    """The error carries the status and the API's own message."""
    client, _, _ = _client({("GET", "/api/v4/x"): json_reply({"message": "401 Unauthorized"}, 401)})
    with pytest.raises(ApiError) as err:
        client.get_json("/x")
    assert (err.value.status, err.value.detail) == (401, "HTTP 401: Unauthorized")  # GitLab's "401 " dropped
    assert str(err.value) == "Test API: HTTP 401: Unauthorized"


def test_an_error_without_a_json_body_falls_back_to_the_reason_phrase():
    """A non-JSON error page is replaced by the status's reason phrase."""
    client, _, _ = _client({("GET", "/api/v4/x"): httpx.Response(502, text="<html>bad gateway</html>")})
    with pytest.raises(ApiError, match=r"HTTP 502: Bad Gateway$"):
        client.get_json("/x")


def test_no_answer_is_an_error_named_by_its_kind():
    """With no response, the error is named by its type."""

    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    client, _, _ = _client({("GET", "/api/v4/x"): refused})
    with pytest.raises(ApiError) as err:
        client.get_json("/x")
    assert (err.value.status, err.value.detail) == (None, "ConnectError")


def test_a_body_that_is_not_json_is_an_error():
    """A body that should be JSON and is not raises an error."""
    client, _, _ = _client({("GET", "/api/v4/x"): httpx.Response(200, text="<html>login</html>")})
    with pytest.raises(ApiError, match="the reply was not JSON"):
        client.get_json("/x")


def test_text_is_decoded_as_utf8_and_a_bad_byte_does_not_cost_the_log():
    """An invalid UTF-8 byte becomes U+FFFD and the rest of the text is kept."""
    client, _, _ = _client({("GET", "/api/v4/log"): httpx.Response(200, content=b"ok \xff done")})
    assert client.get_text("/log") == "ok � done"


def test_a_json_body_is_sent_with_its_method():
    """The body is sent as JSON with the given method."""
    client, api, _ = _client({("PUT", "/api/v4/notes/4"): json_reply({"id": 4})})
    client.send_json("PUT", "/notes/4", {"body": "hi"})
    [sent] = api.requests
    assert sent.headers["content-type"] == "application/json" and sent.content == b'{"body":"hi"}'


# --- pagination -----------------------------------------------------------------


def test_pages_are_followed_by_the_link_header():
    """GitHub style: follow `Link: rel="next"`; the items are in a field of the body."""
    page2 = f"{BASE}/runs/1/jobs?per_page=100&page=2"
    client, api, _ = _client(
        {
            ("GET", "/api/v4/runs/1/jobs"): [
                json_reply({"jobs": [{"id": 1}]}, link=f'<{page2}>; rel="next"'),
                json_reply({"jobs": [{"id": 2}]}),
            ]
        }
    )
    assert [j["id"] for j in client.paginate("/runs/1/jobs", key="jobs")] == [1, 2]
    assert [r.url.params.get("page") for r in api.requests] == [None, "2"]
    assert api.requests[0].url.params["per_page"] == "100"


def test_pages_are_followed_by_x_next_page():
    """GitLab style: follow `x-next-page` until it is empty."""
    client, api, _ = _client(
        {
            ("GET", "/api/v4/jobs"): [
                json_reply([{"id": 1}], x_next_page="2"),
                json_reply([{"id": 2}], x_next_page="3"),
                json_reply([{"id": 3}], x_next_page=""),
            ]
        }
    )
    assert [j["id"] for j in client.paginate("/jobs", scope="failed")] == [1, 2, 3]
    assert [r.url.params["page"] for r in api.requests[1:]] == ["2", "3"]
    assert all(r.url.params["scope"] == "failed" for r in api.requests)


def test_a_listing_that_never_ends_is_an_error_not_a_quiet_cut(monkeypatch):
    """A list longer than the page cap raises an error instead of being cut."""
    monkeypatch.setattr(api_client, "MAX_PAGES", 3)
    client, api, _ = _client({("GET", "/api/v4/jobs"): json_reply([{"id": 1}], x_next_page="2")})
    with pytest.raises(ApiError, match="more than 3 pages"):
        list(client.paginate("/jobs"))
    assert len(api.requests) == 3


# --- rate limits ----------------------------------------------------------------


def test_a_429_waits_for_retry_after_then_retries():
    """A 429 waits for `Retry-After` seconds, then retries."""
    client, _, waits = _client(
        {("GET", "/api/v4/x"): [httpx.Response(429, headers={"retry-after": "2"}), json_reply({"ok": True})]}
    )
    assert client.get_json("/x") == {"ok": True}
    assert waits == [2.0]


def test_retry_after_may_be_an_http_date():
    """A `Retry-After` date is turned into seconds from now."""
    when = "Tue, 14 Nov 2023 22:13:30 GMT"  # NOW + 10s
    client, _, waits = _client(
        {("GET", "/api/v4/x"): [httpx.Response(429, headers={"retry-after": when}), json_reply({})]}
    )
    client.get_json("/x")
    assert waits == [pytest.approx(10.0)]


def test_githubs_exhausted_limit_waits_until_its_reset():
    """A 403 with `x-ratelimit-remaining: 0` waits until `x-ratelimit-reset`."""
    exhausted = json_reply(
        {"message": "API rate limit exceeded"},
        403,
        x_ratelimit_remaining="0",
        x_ratelimit_reset=str(int(NOW) + 10),
    )
    client, _, waits = _client({("GET", "/api/v4/x"): [exhausted, json_reply({})]})
    client.get_json("/x")
    assert waits == [11.0]


def test_githubs_secondary_limit_without_retry_after_waits_a_minute():
    """A secondary rate limit with no headers waits one minute, as GitHub advises."""
    secondary = json_reply({"message": "You have exceeded a secondary rate limit."}, 403)
    client, _, waits = _client({("GET", "/api/v4/x"): [secondary, json_reply({})]})
    client.get_json("/x")
    assert waits == [60.0]


def test_a_limit_that_resets_too_late_fails_saying_when():
    """A wait longer than the cap fails at once and says how long it would be."""
    exhausted = httpx.Response(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW) + 3600)}
    )
    client, _, waits = _client({("GET", "/api/v4/x"): exhausted})
    with pytest.raises(ApiError, match=r"HTTP 403: rate limited; retry in 3601s"):
        client.get_json("/x")
    assert waits == []


def test_a_limit_that_keeps_coming_gives_up_after_the_last_attempt():
    """After the last attempt the rate limit becomes an error."""
    client, api, waits = _client({("GET", "/api/v4/x"): httpx.Response(429, headers={"retry-after": "1"})})
    with pytest.raises(ApiError, match="rate limited"):
        client.get_json("/x")
    assert len(api.requests) == MAX_ATTEMPTS and len(waits) == MAX_ATTEMPTS - 1


def test_a_plain_403_is_not_a_rate_limit():
    """A 403 that is not a rate limit fails without waiting."""
    denied = json_reply({"message": "Must have admin rights to Repository."}, 403)
    client, api, waits = _client({("GET", "/api/v4/x"): denied})
    with pytest.raises(ApiError, match="Must have admin rights"):
        client.get_json("/x")
    assert (len(api.requests), waits) == (1, [])


# --- redirects ------------------------------------------------------------------


@pytest.mark.parametrize("auth", [{"PRIVATE-TOKEN": "tok"}, {"Authorization": "Bearer tok"}])
def test_a_redirect_to_another_host_drops_every_credential(auth):
    """A redirect to another host sends no token and none of the API's headers.

    httpx alone would strip `Authorization` but not GitLab's `PRIVATE-TOKEN`.
    """
    client, api, _ = _client(
        {
            ("GET", "/api/v4/jobs/1/trace"): httpx.Response(
                302, headers={"location": "https://blob.example.net/log?sig=secret"}
            ),
            ("GET", "/log"): httpx.Response(200, text="the log"),
        },
        auth=auth,
    )
    assert client.get_text("/jobs/1/trace") == "the log"
    blob = api.requests[1]
    assert blob.url.host == "blob.example.net"
    assert not {"private-token", "authorization"} & set(blob.headers)
    assert blob.headers["accept"] == "*/*"  # httpx's default, not the API's `Accept`


def test_a_redirect_on_the_same_host_keeps_the_credential():
    """A redirect on the same host keeps the token."""
    client, api, _ = _client(
        {
            ("GET", "/api/v4/projects/old"): httpx.Response(
                301, headers={"location": f"{BASE}/projects/new"}
            ),
            ("GET", "/api/v4/projects/new"): json_reply({"id": 2}),
        }
    )
    assert client.get_json("/projects/old") == {"id": 2}
    assert api.requests[1].headers["private-token"] == "tok"


def test_a_redirect_from_https_to_http_is_refused():
    """A redirect from https to http is refused."""
    client, _, _ = _client(
        {("GET", "/api/v4/x"): httpx.Response(302, headers={"location": "http://git.example.com/api/v4/x"})}
    )
    with pytest.raises(ApiError, match="refused a redirect from https to http"):
        client.get_json("/x")


def test_a_redirect_loop_is_cut():
    """A redirect loop stops at the cap with an error."""
    client, _, _ = _client({("GET", "/api/v4/x"): httpx.Response(302, headers={"location": f"{BASE}/x"})})
    with pytest.raises(ApiError, match="redirects"):
        client.get_json("/x")


def test_an_error_never_names_the_url_it_came_from():
    """An error after a redirect does not include the (signed) URL."""
    client, _, _ = _client(
        {
            ("GET", "/api/v4/jobs/1/trace"): httpx.Response(
                302, headers={"location": "https://blob.example.net/log?sig=secret"}
            ),
            ("GET", "/log"): httpx.Response(403, text="<Error>AuthenticationFailed</Error>"),
        }
    )
    with pytest.raises(ApiError) as err:
        client.get_text("/jobs/1/trace")
    assert "secret" not in str(err.value) and "blob.example.net" not in str(err.value)


# --- TLS ----------------------------------------------------------------------


def test_tls_verification_is_on_by_default_and_can_be_turned_off():
    """Verification is on by default; `verify_ssl: false` turns it off."""
    assert tls_verify(None, True, {}) is True
    assert tls_verify(None, False, {"REQUESTS_CA_BUNDLE": certifi.where()}) is False


def test_a_configured_ca_bundle_is_trusted():
    """`ca_bundle` from the config takes priority over the environment."""
    assert isinstance(tls_verify(certifi.where(), True, {"REQUESTS_CA_BUNDLE": "/nope"}), ssl.SSLContext)


def test_requests_ca_bundle_is_still_honoured():
    """`REQUESTS_CA_BUNDLE` is honoured, as the docs say."""
    assert isinstance(tls_verify(None, True, {"REQUESTS_CA_BUNDLE": certifi.where()}), ssl.SSLContext)


def test_an_unreadable_ca_bundle_fails_naming_it(tmp_path):
    """A CA bundle that cannot be read fails with its path in the message."""
    missing = tmp_path / "corp-ca.pem"
    with pytest.raises(ValueError, match="corp-ca.pem cannot be read"):
        tls_verify(str(missing), True, {})
