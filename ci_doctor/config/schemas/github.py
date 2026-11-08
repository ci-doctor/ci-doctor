"""`github:`: connection settings for GitHub, as a CI system and as a git host."""

from pydantic import Field

from ci_doctor.config.schemas._base import Strict


class GitHubConfig(Strict):
    """How to reach GitHub. Used when GitHub is the ``ci`` or the ``scm``."""

    base_url: str = Field(
        default="https://api.github.com",
        description="GitHub API base URL. Override for GitHub Enterprise, e.g. https://ghe.internal/api/v3.",
    )
    token_env: str = Field(
        default="CI_DOCTOR_GITHUB_TOKEN",
        description="Name of the env var holding a read-only API token. The token itself is never a config value.",
    )
    token_file: str | None = Field(
        default=None, description="Path to a file holding the API token. Wins over token_env."
    )
    ca_bundle: str | None = Field(
        default=None, description="Path to a custom CA bundle for the GitHub endpoint."
    )
    verify_ssl: bool = Field(default=True, description="Verify the GitHub endpoint's TLS certificate.")
    timeout_seconds: int = Field(default=30, description="Per-request timeout for GitHub API calls.")
