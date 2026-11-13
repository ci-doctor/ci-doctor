"""Which CI system a host belongs to: a git remote or a pasted URL picks its adapter.

Matched against the configured APIs only, so `github.com` and `gitlab.com` work with
no config, and a self-hosted instance once the user names it.
"""

import pytest

from ci_doctor.config.loader import load_config
from ci_doctor.providers.hosts import vendor_for_host, web_host


@pytest.mark.parametrize(
    ("remote_host", "vendor"),
    [("github.com", "github"), ("gitlab.com", "gitlab"), ("ghe.corp", None), ("bitbucket.org", None)],
)
def test_the_remote_host_names_the_ci_system(remote_host, vendor):
    """The two public hosts are known with no config; anything else needs the user to name it."""
    assert vendor_for_host(remote_host, load_config(environ={})) == vendor


def test_a_self_hosted_gitlab_in_user_config_is_recognised(_user_config_dir):
    """Naming the instance once makes its remotes resolve to GitLab."""
    (_user_config_dir / "config.yml").write_text("gitlab:\n  base_url: https://git.corp:8443\n")
    cfg = load_config(environ={})
    assert web_host("gitlab", cfg) == "git.corp"
    assert vendor_for_host("git.corp", cfg) == "gitlab"
