"""The one place that talks to a language model.

Agents call two operations:

* ``chat(messages, tools=...)``: a turn that may request tool calls (agent loops)
* ``structured(messages, Schema)``: a turn whose answer is parsed into a Pydantic model

Three providers sit behind ``LLM_PROVIDER``. Groq and Ollama both expose
OpenAI-compatible APIs, so one code path serves them; Azure OpenAI uses its own
client. Everything provider-specific stays in this module.

Structured output strategy: the primary method is JSON-schema structured outputs
(strict) or function calling, depending on the provider. If it fails with an output
problem, the call is retried once with the *other* method. Real runs on Groq showed
both directions are needed: some models don't support JSON schema, while others
(gpt-oss-20b) sometimes answer without calling the function ("tool choice is required,
but model did not call a tool"). Transport problems (rate limits, timeouts) are not
retried here; the SDK already retried them with backoff.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, SecretStr

from marketlens.core.config import Settings, StructuredOutputMethod

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

DEFAULT_STRUCTURED_METHOD: dict[str, StructuredOutputMethod] = {
    "azure_openai": "json_schema",
    "groq": "function_calling",
    "ollama": "function_calling",
}


class LLMError(Exception):
    """Base class for LLM failures. Messages are safe to log, not to show to users."""


class LLMNotConfiguredError(LLMError):
    pass


class LLMOutputError(LLMError):
    """The model answered, but not in the requested shape."""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )

    @classmethod
    def from_message(cls, message: Any) -> TokenUsage:
        usage = getattr(message, "usage_metadata", None) or {}
        return cls(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))


@dataclass(frozen=True)
class StructuredResult(Generic[SchemaT]):
    value: SchemaT
    usage: TokenUsage
    method: StructuredOutputMethod = "json_schema"
    fallback_used: bool = False


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    async def chat(
        self, messages: Sequence[BaseMessage], *, tools: Sequence[BaseTool] = ()
    ) -> AIMessage: ...

    async def structured(
        self, messages: Sequence[BaseMessage], schema: type[SchemaT]
    ) -> StructuredResult[SchemaT]: ...


# Output-shape problems worth retrying with another method. A BadRequest covers
# "response_format json_schema is not supported" and Groq's "json_validate_failed".
_RETRYABLE_OUTPUT_ERRORS = (LLMOutputError, openai.BadRequestError)


class ChatModelClient:
    """``LLMClient`` over any LangChain chat model."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        model_name: str,
        structured_method: StructuredOutputMethod = "json_schema",
        structured_output_fallback: bool = True,
    ) -> None:
        self._model = model
        self._model_name = model_name
        self._method: StructuredOutputMethod = structured_method
        self._fallback = structured_output_fallback

    @property
    def model_name(self) -> str:
        return self._model_name

    async def chat(
        self, messages: Sequence[BaseMessage], *, tools: Sequence[BaseTool] = ()
    ) -> AIMessage:
        runnable: Any = self._model.bind_tools(list(tools)) if tools else self._model
        result = await runnable.ainvoke(list(messages))
        if not isinstance(result, AIMessage):
            raise LLMOutputError(f"expected AIMessage, got {type(result).__name__}")
        return result

    async def structured(
        self, messages: Sequence[BaseMessage], schema: type[SchemaT]
    ) -> StructuredResult[SchemaT]:
        try:
            return await self._structured(messages, schema, self._method)
        except _RETRYABLE_OUTPUT_ERRORS as exc:
            if not self._fallback:
                raise
            other: StructuredOutputMethod = (
                "function_calling" if self._method == "json_schema" else "json_schema"
            )
            logger.warning(
                "structured output failed; retrying with the other method",
                extra={
                    "schema": schema.__name__,
                    "method": self._method,
                    "fallback": other,
                    "error": type(exc).__name__,
                },
            )
            result = await self._structured(messages, schema, other)
            return StructuredResult(result.value, result.usage, result.method, fallback_used=True)

    async def _structured(
        self,
        messages: Sequence[BaseMessage],
        schema: type[SchemaT],
        method: StructuredOutputMethod,
    ) -> StructuredResult[SchemaT]:
        runnable = self._model.with_structured_output(
            schema,
            method=method,
            # Strict decoding for JSON schema; strict tool schemas are not accepted by
            # every OpenAI-compatible provider, so function calling stays lenient and
            # Pydantic validates the result instead.
            strict=True if method == "json_schema" else None,
            include_raw=True,
        )
        output: Any = await runnable.ainvoke(list(messages))
        parsed = output.get("parsed") if isinstance(output, dict) else None
        if output.get("parsing_error") is not None or parsed is None:
            # Includes refusals and answers with no tool call: the model broke the shape.
            raise LLMOutputError(
                f"{schema.__name__} could not be parsed ({method}): {output.get('parsing_error')!r}"
            )
        value = parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
        return StructuredResult(value, TokenUsage.from_message(output.get("raw")), method)


def build_llm_client(settings: Settings) -> ChatModelClient:
    """Build the client for ``LLM_PROVIDER``. Raises if that provider isn't configured."""
    llm = settings.llm
    provider = llm.provider
    common: dict[str, Any] = {
        "temperature": llm.temperature,
        "max_retries": llm.max_retries,
    }

    model: BaseChatModel
    if provider == "azure_openai":
        azure = settings.azure_openai
        if not azure.is_configured:
            raise LLMNotConfiguredError(
                "LLM_PROVIDER=azure_openai needs AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_CHAT_DEPLOYMENT"
            )
        # Canonical field names (not aliases), so the call is fully type-checked.
        model = AzureChatOpenAI(
            azure_endpoint=azure.endpoint,
            deployment_name=azure.chat_deployment,
            openai_api_version=azure.api_version,
            openai_api_key=azure.api_key,
            request_timeout=llm.request_timeout_seconds,
            **common,
        )
        name = azure.chat_deployment or "azure"
    elif provider == "groq":
        if settings.groq.api_key is None:
            raise LLMNotConfiguredError("LLM_PROVIDER=groq needs GROQ_API_KEY")
        model = ChatOpenAI(
            model_name=settings.groq.model,
            openai_api_base=settings.groq.base_url,
            openai_api_key=settings.groq.api_key,
            request_timeout=llm.request_timeout_seconds,
            **common,
        )
        name = f"groq:{settings.groq.model}"
    else:  # ollama
        model = ChatOpenAI(
            model_name=settings.ollama.model,
            openai_api_base=settings.ollama.base_url,
            # Ollama ignores the key, but the OpenAI client requires one.
            openai_api_key=SecretStr("ollama"),
            request_timeout=llm.request_timeout_seconds,
            **common,
        )
        name = f"ollama:{settings.ollama.model}"

    method = llm.structured_output or DEFAULT_STRUCTURED_METHOD[provider]
    logger.info("llm client ready", extra={"provider": provider, "model": name, "method": method})
    return ChatModelClient(
        model,
        model_name=name,
        structured_method=method,
        structured_output_fallback=llm.structured_output_fallback,
    )
