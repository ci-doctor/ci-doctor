"""Why a job log could not be downloaded, said safely.

Shared by every adapter, so the wording is the same whoever failed. Lives here
rather than in `core/` because it reads vendor exceptions; it names no provider,
and reads their status codes by duck typing instead of importing their SDKs.

The exception's own text is never used: GitHub serves a log through a short-lived
signed URL, and an HTTP error's message carries that URL, query string and all. A
status code and an exception type say enough to act on and leak nothing.
"""

#: Status codes with a fix the user can act on, phrased as that fix.
_HINTS = {
    401: "not authenticated: set an API token with read access",
    403: "forbidden or rate-limited: check the token's scopes, or wait and retry",
    404: "not found: the log has expired, or the token cannot see this repository",
    410: "gone: the log has expired",
    429: "rate-limited: wait and retry",
}


def describe(exc: BaseException) -> str:
    """Turn a failed log fetch into one line safe to show and to log.

    Args:
        exc: Whatever the adapter caught.

    Returns:
        "HTTP <code>: <hint>" when a status code is known, else the exception's
        type name. Never the exception's message.
    """
    status = _status(exc)
    if status is None:
        return f"log download failed ({type(exc).__name__})"
    hint = _HINTS.get(status, "the CI API refused the request" if status < 500 else "the CI API failed")
    return f"HTTP {status}: {hint}"


def _status(exc: BaseException) -> int | None:
    """Find an HTTP status code on an SDK's exception, whichever SDK raised it.

    Args:
        exc: The caught exception.

    Returns:
        The status code, or None when the exception carries none (a timeout, a
        refused connection).
    """
    response = getattr(exc, "response", None)
    for value in (
        getattr(exc, "status", None),  # PyGithub
        getattr(exc, "response_code", None),  # python-gitlab
        getattr(response, "status_code", None),  # requests
    ):
        if isinstance(value, int):
            return value
    return None
