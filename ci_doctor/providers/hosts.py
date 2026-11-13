"""Which API host a token may be sent to, and which CI system a git host is.

A token is only ever sent to a host the user chose. Either they named it themselves,
in their own config or environment (which a repository cannot set, see
`config.loader.UNTRUSTED_REPO_KEYS`), or it is a built-in public API whose web host
is the checkout's own remote: `api.github.com` for a repository on `github.com`.
So a repository cannot steer a token, and a token for one host is never offered to
another because a pasted URL said so.

Shared by every adapter. Lives here rather than in `core/` because it names vendors.
"""

from urllib.parse import urlsplit

from ci_doctor.config.schemas import Config

#: The public API host each built-in vendor defaults to -> its web (git remote) host.
#: A self-hosted instance is never built in: its user names it in config.
BUILT_IN_API = {"api.github.com": "github.com", "gitlab.com": "gitlab.com"}

#: Vendor -> the config block holding its connection settings.
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


def user_named(vendor: str, cfg: Config) -> bool:
    """Whether the user chose the vendor's API host, rather than leaving the default.

    Under `load_untrusted_repo_config` the repo cannot set a base URL, so one that
    differs from the shipped default came from the user's config or environment.

    Args:
        vendor: `github` or `gitlab`.
        cfg: The effective config.

    Returns:
        True when the base URL is not the built-in default.
    """
    block = getattr(cfg, vendor)
    return block.base_url != type(block).model_fields["base_url"].default


def may_send_token(vendor: str, cfg: Config, remote_host: str | None) -> bool:
    """Whether a token for `vendor` may go to its configured API for this checkout.

    Args:
        vendor: `github` or `gitlab`.
        cfg: The effective config, loaded so the repo could not set connection keys.
        remote_host: The checkout's git remote host, or None when there is none.

    Returns:
        True when the user named the API host, or the built-in host serves the
        checkout's own remote.
    """
    if user_named(vendor, cfg):
        return True
    return remote_host is not None and web_host(vendor, cfg) == remote_host


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
