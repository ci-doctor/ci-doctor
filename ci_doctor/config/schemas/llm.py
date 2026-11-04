"""`llm:`: the optional explanation step, and the settings blocks of the backends that need them."""

from typing import Literal

from pydantic import Field, model_validator

from ci_doctor.config.schemas._base import Strict

#: The settings block each cloud backend requires.
_BACKEND_BLOCK = {
    "azure": "azure",
    "bedrock": "aws",
    "bedrock-mantle": "aws",
    "google-cloud": "gcp",
}


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


class LLMConfig(Strict):
    """The optional explanation step.

    Disabled or unreachable degrades to the deterministic report rather than
    failing the run.
    """

    enabled: bool = Field(
        default=True, description="Set false for a deterministic-only report with no LLM call."
    )
    backend: Literal[
        "openai",
        "anthropic",
        "azure",
        "bedrock",
        "bedrock-mantle",
        "google",
        "google-cloud",
        "groq",
        "mistral",
        "cohere",
        "xai",
        "huggingface",
        "litellm",
    ] = Field(
        default="openai",
        description=(
            "Which provider to call. openai: any OpenAI-compatible endpoint (needs api_base). "
            "azure, bedrock, bedrock-mantle, google-cloud: cloud auth plus their own settings. "
            "litellm: any other provider, by its model string, e.g. vertex_ai/gemini-1.5-pro."
        ),
    )
    model: str | None = Field(default=None, description='Model identifier, e.g. "qwen2.5-coder:32b".')
    api_base: str | None = Field(
        default=None, description="Base URL of the OpenAI-compatible / litellm endpoint."
    )
    api_key_env: str | None = Field(
        default=None,
        description="Name of the env var holding the LLM API key. Often unset — local servers need no key, "
        "and a hosted backend without this set falls back to its own default env var (e.g. ANTHROPIC_API_KEY).",
    )
    ca_bundle: str | None = Field(
        default=None, description="CA bundle for the LLM endpoint, independent of the CI provider's."
    )
    openai: OpenAiConfig = Field(
        default_factory=OpenAiConfig, description="Settings for backend: openai. Optional."
    )
    azure: AzureConfig | None = Field(
        default=None, description="Settings for backend: azure. Required there."
    )
    aws: AwsConfig | None = Field(
        default=None, description="Settings for backend: bedrock or bedrock-mantle. Required there."
    )
    gcp: GcpConfig | None = Field(
        default=None, description="Settings for backend: google-cloud. Required there."
    )
    max_input_tokens: int = Field(
        default=12000,
        description="Evidence budget. Larger bundles are truncated, and every truncation is visible in the report.",
    )
    temperature: float = Field(
        default=0.1, description="Sampling temperature. Low keeps the postmortem reproducible."
    )
    timeout_seconds: int = Field(
        default=120,
        description="Wall-clock budget for one LLM call, schema-repair retry and SDK retries included. "
        "Also the per-request timeout.",
    )
    max_retries: int = Field(
        default=1,
        ge=0,
        description="HTTP-transport retries per request, set on the provider's SDK client. Applies to "
        "providers whose SDK has the setting (openai, azure, anthropic, groq, ...); bedrock and "
        "litellm keep their own. Kept low: Pydantic AI's own Agent already retries once on a "
        "schema-invalid reply, and the two multiply.",
    )

    @model_validator(mode="after")
    def _backend_has_its_settings(self) -> "LLMConfig":
        """A backend that takes a settings block must be given one."""
        block = _BACKEND_BLOCK.get(self.backend)
        if block and getattr(self, block) is None:
            raise ValueError(f"llm.backend '{self.backend}' requires an `llm.{block}:` block")
        return self

    @property
    def provider_settings(self) -> Strict | None:
        """The settings block of the chosen backend, or None if it takes none."""
        block = _BACKEND_BLOCK.get(self.backend)
        return getattr(self, block) if block else None
