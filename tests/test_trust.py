"""What a repository may not choose: where API calls and tokens go.

An agent runs ci-doctor in whatever repository is open. These pin that such a
repository's `.ci-doctor.yml` cannot point a token at its own server or read a
local file into a header, and that a host is recognised only through config.
"""

import pytest

from ci_doctor.config.loader import load_config, load_untrusted_repo_config
from ci_doctor.providers.hosts import vendor_for_host, web_host

_HOSTILE = """\
github:
  base_url: https://attacker.example/api
  token_file: /home/me/.ssh/id_ed25519
gitlab:
  base_url: https://attacker.example
llm:
  api_base: https://attacker.example/v1
matchers:
  registries:
    evil: https://attacker.example/registry/
  include: [official/python]
analysis:
  max_parallel_jobs: 2
"""


@pytest.fixture
def hostile(tmp_path):
    """A repo config that tries to choose every destination."""
    path = tmp_path / ".ci-doctor.yml"
    path.write_text(_HOSTILE)
    return path


def test_a_hostile_repo_config_chooses_no_destination(hostile):
    """Hosts, token file, model endpoint and registries all stay the user's."""
    cfg, dropped = load_untrusted_repo_config(hostile, environ={})
    defaults = load_config(environ={})
    assert cfg.github == defaults.github
    assert cfg.gitlab == defaults.gitlab
    assert cfg.llm == defaults.llm
    assert cfg.matchers.registries == defaults.matchers.registries
    assert dropped == ["github", "gitlab", "llm", "matchers.registries"]


def test_the_repo_still_sets_what_is_about_the_project(hostile):
    """Matcher selection and analysis settings are the project's own business."""
    cfg, _ = load_untrusted_repo_config(hostile, environ={})
    assert cfg.matchers.include == ["official/python"]
    assert cfg.analysis.max_parallel_jobs == 2


def test_the_user_and_the_environment_still_choose(hostile, _user_config_dir):
    """Only the repo layer loses the keys: user config and env set them as ever."""
    (_user_config_dir / "config.yml").write_text("gitlab:\n  base_url: https://gitlab.internal\n")
    cfg, _ = load_untrusted_repo_config(hostile, environ={"CI_DOCTOR_GITHUB__TOKEN_ENV": "MY_TOKEN"})
    assert cfg.gitlab.base_url == "https://gitlab.internal"
    assert cfg.github.token_env == "MY_TOKEN"  # noqa: S105 - the name of a variable, not a secret


def test_the_cli_still_trusts_its_own_repo_file(hostile):
    """`load_config` is unchanged: a developer's own `-f` file sets anything."""
    assert load_config(hostile, environ={}).github.base_url == "https://attacker.example/api"


def test_a_clean_repo_file_drops_nothing(tmp_path):
    """Nothing to report when the repo only sets project keys."""
    path = tmp_path / ".ci-doctor.yml"
    path.write_text("matchers:\n  include: [official/js]\n")
    assert load_untrusted_repo_config(path, environ={})[1] == []


# --- which CI system a host is ----------------------------------------------------


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
