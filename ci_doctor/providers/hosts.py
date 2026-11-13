"""Which CI system a host belongs to, so a URL or a git remote picks its adapter.

A host is matched against each vendor's *configured* API, never taken from the URL
itself: the API called is the shipped default or one named in config. A pasted URL
on any other host matches no vendor and is refused before an adapter exists.

Shared by every adapter. Lives here rather than in `core/` because it names vendors.
"""

from urllib.parse import urlsplit

from ci_doctor.config.schemas import Config

#: The public API host each built-in vendor defaults to -> its web (git remote) host.
#: A self-hosted instance is never built in: its user names it in config.
BUILT_IN_API = {"api.github.com": "github.com", "gitlab.com": "gitlab.com"}

#: The vendors with a connection block in config (`github:`, `gitlab:`), in lookup order.
_VENDORS = ("github", "gitlab")


def host_of(url: str | None) -> str | None:
    """The lower-cased hostname of a URL, without user or port.

    Args:
        url: An API base URL or web URL.

    Returns:
        The host, or None for a blank or host-less URL.
    """
    return (urlsplit(url).hostname or None) if url else None


def web_host(vendor: str, cfg: Config) -> str | None:
    """The web host a vendor's configured API serves, the one its git remotes name.

    `api.github.com` serves `github.com`; a GitHub Enterprise `ghe.example/api/v3`
    and every GitLab serve their own host.

    Args:
        vendor: `github` or `gitlab`.
        cfg: The effective config.

    Returns:
        The host, or None when the vendor's base URL is blank.
    """
    api = host_of(getattr(cfg, vendor).base_url)
    return BUILT_IN_API.get(api, api) if api else None


def vendor_for_host(remote_host: str, cfg: Config) -> str | None:
    """Which CI system a git remote's host belongs to.

    Matched against each vendor's configured API, so a self-hosted GitLab named in
    user config is recognised, and `github.com` / `gitlab.com` work with no config.

    Args:
        remote_host: The checkout's git remote host.
        cfg: The effective config.

    Returns:
        `github` or `gitlab`, or None when no configured API serves that host. The
        caller then falls back to `ci` from config.
    """
    return next((v for v in _VENDORS if web_host(v, cfg) == remote_host), None)
