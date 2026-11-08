"""Picking a transport by location."""

import pytest

from ci_doctor.errors import SetupError
from ci_doctor.matchers.registry import registry_dir
from ci_doctor.matchers.transports import transport_for


@pytest.mark.parametrize(
    ("location", "kind"),
    [
        ("/srv/reg", "LocalTransport"),
        ("C:/reg", "LocalTransport"),
        ("file:///srv/reg", "LocalTransport"),
        ("https://h/reg/", "HttpTransport"),
        ("http://h/reg/", "HttpTransport"),
    ],
)
def test_the_transport_is_picked_by_the_location(location, kind):
    """One lookup, not an if-chain per caller."""
    assert type(transport_for(location)).__name__ == kind


@pytest.mark.parametrize("location", ["ftp://h/x", "git+https://h/r.git", "ssh://h/r"])
def test_an_unsupported_scheme_is_refused(location):
    """Only paths, `file://` and `http(s)://`."""
    with pytest.raises(SetupError, match="unsupported scheme"):
        registry_dir("r", location)


def test_credentials_in_a_refused_location_are_redacted():
    """The error quotes the location, never its userinfo."""
    with pytest.raises(SetupError) as err:
        registry_dir("r", "ftp://user:s3cret@h/x")
    assert "s3cret" not in str(err.value)
