"""LLM backend registry.

Every backend builds one `PydanticAILLMClient` wrapping a `pydantic_ai.models.Model`,
selected by `llm.backend`. Each wraps the caller's output type in `PromptedOutput` per
call, with one built-in repair retry (`Agent(retries=1)`) — no hand-rolled JSON parsing
or retry loop here.

Any Pydantic AI-native provider goes through one generic path: Pydantic AI's own
`infer_model` picks the `Model` class, and the provider is built from the constructor
arguments it actually accepts (`api_key`, `base_url`, `http_client`). Adding a provider
is a name in `LLMConfig.backend` and a pip extra. A provider with settings of its own
adds a block under `config/types/` that dumps to its constructor arguments (see
`LLMConfig.provider_settings`). Two backends override the generic path: `openai` picks
Chat Completions or the Responses API, and `litellm` is the community `pydantic-ai-litellm`
bridge, which routes in-process to ~100 providers by litellm's own model-string
convention (`vertex_ai/gemini-1.5-pro`).

Every backend needs its own pip extra; there is no default SDK in the base install.
`openai`/`azure` and `litellm` are mutually exclusive — litellm pins `openai<3.0`,
Pydantic AI's OpenAI integration needs `openai>=3.8` (see `[tool.uv] conflicts`).

Every SDK is imported inside the builder that needs it, so importing this module
costs nothing on a run that never reaches a model.
"""

import asyncio
import contextlib
import inspect
import logging
import os
import ssl
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import TYPE_CHECKING, Any, TypeVar, get_args

from pydantic import BaseModel

from ci_doctor.config.schema import LLMConfig
from ci_doctor.core.ports import LLMClient

log = logging.getLogger("ci_doctor.llm")

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider

#: Every name `llm.backend` accepts — the config Literal is the single source.
_KNOWN = frozenset(get_args(LLMConfig.model_fields["backend"].annotation))


def _api_key(cfg: LLMConfig, environ: Mapping[str, str]) -> str | None:
    """Resolve the configured API key, or None to let the provider use its own default env var."""
    return environ.get(cfg.api_key_env) if cfg.api_key_env else None


def _require_model(cfg: LLMConfig) -> str:
    """Narrow `cfg.model` to `str` — `backend_ready` checks this in practice, but an injected client skips it."""
    if not cfg.model:
        raise ValueError(f"llm.model is required for the {cfg.backend} backend")
    return cfg.model


def _provider_kwargs(provider_class: type, cfg: LLMConfig, environ: Mapping[str, str]) -> dict[str, Any]:
    """Build the constructor arguments the provider class actually accepts."""
    params = inspect.signature(provider_class.__init__).parameters
    kwargs: dict[str, Any] = {}
    if "api_key" in params:
        kwargs["api_key"] = _api_key(cfg, environ)
    if "base_url" in params and cfg.api_base:
        kwargs["base_url"] = cfg.api_base
    if "http_client" in params and cfg.ca_bundle:
        import httpx

        kwargs["http_client"] = httpx.AsyncClient(verify=ssl.create_default_context(cafile=cfg.ca_bundle))
    return kwargs


@contextlib.contextmanager
def _aws_ca_bundle(path: str | None) -> Iterator[None]:
    """Expose `llm.ca_bundle` to boto as `AWS_CA_BUNDLE` while the Bedrock provider builds its client.

    The provider has no `http_client`, and handing it a ready-made boto client would skip its
    own setup (bearer-token auth, 300s read timeout). boto reads the variable once, at client
    creation, so it is restored straight after.
    """
    if not path:
        yield
        return
    previous = os.environ.get("AWS_CA_BUNDLE")
    os.environ["AWS_CA_BUNDLE"] = path
    try:
        yield
    finally:
        if previous is None:
            del os.environ["AWS_CA_BUNDLE"]
        else:
            os.environ["AWS_CA_BUNDLE"] = previous


_M = TypeVar("_M", bound=BaseModel)


def _apply_max_retries(provider: object, cfg: LLMConfig) -> None:
    """Hand `llm.max_retries` to the provider's SDK client, where that SDK has the knob.

    The openai/anthropic/groq SDKs retry 429s, 5xxs and connection errors themselves
    (default 2); leaving that alone would multiply with the Agent's own repair retry.
    Providers without a `max_retries` client attribute (bedrock via boto, litellm) keep
    their own retry behavior.
    """
    client = getattr(provider, "client", None)
    if client is not None and hasattr(client, "max_retries"):
        client.max_retries = cfg.max_retries


def _build_provider(cfg: LLMConfig, environ: Mapping[str, str]) -> "Provider[Any]":
    """Build the Pydantic AI provider for `cfg.backend` from the config."""
    from pydantic_ai.models import infer_provider_class

    provider_class = infer_provider_class(cfg.backend)
    kwargs = _provider_kwargs(provider_class, cfg, environ)
    if cfg.backend == "openai":
        # The OpenAI SDK refuses an empty key; local servers ignore whatever they get.
        kwargs["api_key"] = kwargs.get("api_key") or "no-key"
    if block := cfg.provider_settings:
        # AWS auth itself comes from the environment/IAM (boto3's chain), not an API key.
        kwargs.update(block.model_dump(by_alias=True, exclude_none=True))
    with _aws_ca_bundle(cfg.ca_bundle if cfg.backend == "bedrock" else None):
        provider = provider_class(**kwargs)
    _apply_max_retries(provider, cfg)
    return provider


def _generic_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Any Pydantic AI-native provider: Anthropic, Google, Groq, Mistral, Cohere, xAI, Bedrock, ..."""
    from pydantic_ai.models import infer_model

    model_name = _require_model(cfg)
    provider = _build_provider(cfg, environ)
    return infer_model(f"{cfg.backend}:{model_name}", provider_factory=lambda _: provider)


def _openai_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """An OpenAI-compatible endpoint, over Chat Completions or the Responses API (`llm.openai.api`)."""
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

    model_name = _require_model(cfg)
    provider = _build_provider(cfg, environ)
    model_class = OpenAIChatModel if cfg.openai.api == "chat" else OpenAIResponsesModel
    return model_class(model_name, provider=provider)


def _litellm_model(cfg: LLMConfig, environ: Mapping[str, str]) -> "Model":
    """Anything litellm reaches that the native providers can't."""
    from pydantic_ai_litellm import LiteLLMModel  # ty: ignore[unresolved-import]

    model_name = _require_model(cfg)
    if cfg.ca_bundle:
        import litellm  # ty: ignore[unresolved-import]

        litellm.ssl_verify = (
            cfg.ca_bundle
        )  # litellm's only CA knob; a module global, but we are the only caller

    return LiteLLMModel(model_name, api_key=_api_key(cfg, environ), api_base=cfg.api_base or None)


_OVERRIDES: dict[str, Callable[[LLMConfig, Mapping[str, str]], "Model"]] = {
    "openai": _openai_model,
    "litellm": _litellm_model,
}

#: What a backend needs before a call is worth attempting; the default is just a model.
_NEEDS: dict[str, Callable[[LLMConfig], bool]] = {
    "openai": lambda cfg: bool(cfg.model and cfg.api_base),
}


#: The pip extra for a backend whose name differs from it.
_EXTRA = {"azure": "openai", "google-cloud": "google"}


def make_client(cfg: LLMConfig, environ: Mapping[str, str] | None = None) -> LLMClient:
    """Build the client for the configured backend.

    Args:
        cfg: LLM settings; `cfg.backend` selects the implementation.
        environ: Environment for API keys. Defaults to os.environ.

    Returns:
        A client implementing :class:`~ci_doctor.core.ports.LLMClient`.

    Raises:
        ValueError: On an unknown backend name.
    """
    if cfg.backend not in _KNOWN:
        raise ValueError(f"unknown llm.backend: {cfg.backend}")
    build = _OVERRIDES.get(cfg.backend, _generic_model)
    resolved_environ = os.environ if environ is None else environ
    try:
        model = build(cfg, resolved_environ)
    except ImportError as exc:
        extra = _EXTRA.get(cfg.backend, cfg.backend)
        raise ImportError(
            f"llm.backend {cfg.backend!r} needs its SDK: pip install 'ci-doctorr[{extra}]' ({exc})"
        ) from exc
    return PydanticAILLMClient(model, cfg)


def backend_ready(cfg: LLMConfig) -> bool:
    """Check whether a backend can run as configured.

    Args:
        cfg: LLM settings.

    Returns:
        True if the backend has everything it needs. Unknown backends are False.
    """
    if cfg.backend not in _KNOWN:
        return False
    return _NEEDS.get(cfg.backend, lambda c: bool(c.model))(cfg)


#: How long `close()` waits for the SDK client to shut down before stopping the loop anyway.
_CLOSE_WAIT_SECONDS = 5


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Thread body: run `loop` until it is stopped, then release it."""
    try:
        loop.run_forever()
    finally:
        loop.close()


class PydanticAILLMClient(LLMClient):
    """Wraps one `pydantic_ai.models.Model` behind the `LLMClient` port.

    One `Agent` is built lazily and reused for the client's lifetime (one client per
    run, per `llm/report.py::client_for_run`). Jobs call in from a thread pool, but every
    call runs on one shared event loop in a daemon thread: the SDK's async client pools
    keep-alive connections, and a pooled connection is bound to the loop that opened it,
    so `run_sync` (a fresh loop per call) drops some calls with "Connection error".
    """

    def __init__(self, model: "Model", cfg: LLMConfig) -> None:
        """Store the model and config; no Agent or loop is built until a call is made."""
        self._model = model
        self.cfg = cfg
        self._agent: Agent[None, Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _agent_for(self) -> "Agent[None, Any]":
        """Build the Agent once, thread-safely (jobs run on a thread pool)."""
        with self._lock:
            if self._agent is None:
                from pydantic_ai import Agent

                self._agent = Agent(
                    self._model,
                    retries=1,
                    model_settings={
                        "temperature": self.cfg.temperature,
                        "timeout": self.cfg.timeout_seconds,
                    },
                )
        return self._agent

    def _loop_for(self) -> asyncio.AbstractEventLoop:
        """Start the shared event loop once, thread-safely."""
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=_run_loop, args=(loop,), name="ci-doctor-llm", daemon=True).start()
                self._loop = loop
        return self._loop

    async def _close_sdk_client(self) -> None:
        """Close the model's SDK client — and with it the `httpx` client `ca_bundle` built."""
        client = getattr(self._model, "client", None)
        closer = getattr(client, "aclose", None) or getattr(client, "close", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                await result

    def close(self) -> None:
        """Close the SDK client and stop the loop thread. The client is done after this.

        Best-effort: a failure to close is logged, never raised — the run's report is
        already built by the time this is called.
        """
        with self._lock:
            loop, self._loop = self._loop, None
        if loop is None:  # no call was ever made, so nothing was opened
            return
        try:
            asyncio.run_coroutine_threadsafe(self._close_sdk_client(), loop).result(_CLOSE_WAIT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - closing must never fail the run
            log.debug("closing the LLM client failed: %s", exc)
        finally:
            loop.call_soon_threadsafe(loop.stop)

    def complete_structured(self, prompt: str, output_type: type[_M]) -> _M:
        """Run one completion and return the reply validated as `output_type`.

        Args:
            prompt: The rendered, already-redacted prompt.
            output_type: The pydantic model the reply must match.

        Returns:
            The reply, already schema-valid.

        Raises:
            Exception: Any transport, API, or exhausted-retry failure, or `TimeoutError`
                when the call — repair retry and SDK retries included — outlasts
                `llm.timeout_seconds`.
        """
        from pydantic_ai.output import PromptedOutput

        agent = self._agent_for()
        run = agent.run(prompt, output_type=PromptedOutput(output_type))
        budget = asyncio.wait_for(run, self.cfg.timeout_seconds)
        result = asyncio.run_coroutine_threadsafe(budget, self._loop_for()).result()
        return result.output
