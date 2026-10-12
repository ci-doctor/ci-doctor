"""Typed sub-models of the config schema."""

from ci_doctor.config.types.base import Strict
from ci_doctor.config.types.ci_providers import GitHubConfig, GitLabConfig
from ci_doctor.config.types.llm_provider import AwsConfig, AzureConfig, GcpConfig, OpenAiConfig

__all__ = ["AwsConfig", "AzureConfig", "GcpConfig", "GitHubConfig", "GitLabConfig", "OpenAiConfig", "Strict"]
