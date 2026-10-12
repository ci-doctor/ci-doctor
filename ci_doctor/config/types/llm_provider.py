"""Settings blocks for the LLM backends that need more than a model and a key."""

from typing import Literal

from pydantic import Field

from ci_doctor.config.types.base import Strict


class OpenAiConfig(Strict):
    """Settings for the openai backend."""

    api: Literal["chat", "responses"] = Field(
        default="chat",
        description="Which OpenAI API to call. chat: Chat Completions, which every OpenAI-compatible "
        "server speaks. responses: the newer Responses API, which OpenAI itself and only some servers offer.",
    )


class AzureConfig(Strict):
    """Settings for the azure backend.

    ``serialization_alias`` is the provider's own constructor argument name: dumping
    the block by alias yields exactly the kwargs the provider takes.
    """

    endpoint: str = Field(
        serialization_alias="azure_endpoint", description="Azure OpenAI resource endpoint URL."
    )
    api_version: str = Field(description="Azure OpenAI API version, e.g. 2024-10-21.")


class AwsConfig(Strict):
    """Settings for the bedrock and bedrock-mantle backends (aliased as in :class:`AzureConfig`)."""

    region: str | None = Field(
        default=None,
        serialization_alias="region_name",
        description="AWS region. Falls back to AWS_DEFAULT_REGION/AWS_REGION if unset.",
    )


class GcpConfig(Strict):
    """Settings for the google-cloud (Vertex AI) backend."""

    project: str | None = Field(
        default=None, description="Google Cloud project. Falls back to the environment's."
    )
    location: str | None = Field(
        default=None, description="Google Cloud location, e.g. us-central1 or global."
    )
