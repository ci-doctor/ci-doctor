"""`gitlab:`: connection settings for GitLab, as a CI system and as a git host."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict


class GitLabConfig(Strict):
    """How to reach GitLab. Used when GitLab is the ``ci`` or the ``scm``."""

    base_url: str = Field(
        default="https://gitlab.com",
        description="GitLab base URL. Override for a self-hosted or internal instance.",
    )
    api_version: str = Field(default="v4", description="GitLab REST API version.")
    token_env: str = Field(
        default="CI_DOCTOR_GITLAB_TOKEN",
        description="Name of the env var holding a read-only API token. The token itself is never a config value.",
    )
    token_file: str | None = Field(
        default=None,
        description="Path to a file holding the API token, e.g. /run/secrets/gitlab_token. Wins over token_env.",
    )
    ca_bundle: str | None = Field(
        default=None, description="Path to a custom CA bundle for the GitLab endpoint."
    )
    verify_ssl: bool = Field(default=True, description="Verify the GitLab endpoint's TLS certificate.")
    timeout_seconds: int = Field(default=30, description="Per-request timeout for GitLab API calls.")
