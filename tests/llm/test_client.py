"""LLM client behaviour against a stub chat model (no network), and provider selection."""

from __future__ import annotations

from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, SecretStr

from marketlens.core.config import (
    AzureOpenAISettings,
    GroqSettings,
    LLMSettings,
    OllamaSettings,
    Settings,
)
from marketlens.llm import ChatModelClient, LLMNotConfiguredError, LLMOutputError, build_llm_client

pytestmark = pytest.mark.anyio


class Verdict(BaseModel):
    answer: str


def _ok(value: BaseModel, inp: int = 120, out: int = 30) -> dict[str, Any]:
    usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    return {
        "raw": AIMessage(content="", usage_metadata=usage),
        "parsed": value,
        "parsing_error": None,
    }


def _bad_request(message: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    return openai.BadRequestError(
        message, response=httpx.Response(400, request=request), body={"error": {"message": message}}
    )


class StubModel:
    """Implements the slice of BaseChatModel the client uses. Replies per method."""

    def __init__(self, by_method: dict[str, Any] | None = None, chat_output: Any = None) -> None:
        self.by_method = by_method or {}
        self.chat_output = chat_output or AIMessage(content="hi")
        self.bound_tools: list[str] = []
        self.structured_calls: list[dict[str, Any]] = []

    def bind_tools(self, tools: list[BaseTool]) -> RunnableLambda[Any, Any]:
        self.bound_tools = [t.name for t in tools]
        return RunnableLambda(lambda _: self.chat_output)

    async def ainvoke(self, messages: Any) -> Any:
        return self.chat_output

    def with_structured_output(self, schema: type, **kwargs: Any) -> RunnableLambda[Any, Any]:
        self.structured_calls.append({"schema": schema, **kwargs})
        reply = self.by_method[kwargs["method"]]

        def run(_: Any) -> Any:
            if isinstance(reply, Exception):
                raise reply
            return reply

        return RunnableLambda(run)


def _client(model: StubModel, **kwargs: Any) -> ChatModelClient:
    return ChatModelClient(model, model_name="stub", **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------ structured output


async def test_json_schema_is_strict_and_reports_usage() -> None:
    model = StubModel({"json_schema": _ok(Verdict(answer="yes"))})

    result = await _client(model).structured([HumanMessage("q")], Verdict)

    assert result.value == Verdict(answer="yes")
    assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 30)
    assert result.method == "json_schema"
    assert not result.fallback_used
    assert model.structured_calls == [
        {"schema": Verdict, "method": "json_schema", "strict": True, "include_raw": True}
    ]


@pytest.mark.parametrize(
    "failure",
    [
        {"raw": AIMessage(content="no"), "parsed": None, "parsing_error": ValueError("bad json")},
        {"raw": AIMessage(content="I can't help"), "parsed": None, "parsing_error": None},
        _bad_request("response_format json_schema is not supported by this model"),
        _bad_request("json_validate_failed: Failed to generate JSON"),
    ],
)
async def test_falls_back_to_function_calling_on_output_failures(failure: Any) -> None:
    model = StubModel({"json_schema": failure, "function_calling": _ok(Verdict(answer="ok"))})

    result = await _client(model).structured([HumanMessage("q")], Verdict)

    assert result.value == Verdict(answer="ok")
    assert result.method == "function_calling"
    assert result.fallback_used
    assert [c["method"] for c in model.structured_calls] == ["json_schema", "function_calling"]
    assert model.structured_calls[1]["strict"] is None  # lenient tools; Pydantic validates


async def test_no_fallback_when_disabled() -> None:
    model = StubModel({"json_schema": _bad_request("unsupported")})

    with pytest.raises(openai.BadRequestError):
        await _client(model, structured_output_fallback=False).structured(
            [HumanMessage("q")], Verdict
        )


async def test_function_calling_primary_falls_back_to_json_schema() -> None:
    # Seen on Groq with gpt-oss-20b: "Tool choice is required, but model did not call a tool".
    no_call = _bad_request("Tool choice is required, but model did not call a tool")
    model = StubModel({"function_calling": no_call, "json_schema": _ok(Verdict(answer="ok"))})

    result = await _client(model, structured_method="function_calling").structured(
        [HumanMessage("q")], Verdict
    )

    assert result.value == Verdict(answer="ok")
    assert result.method == "json_schema"
    assert result.fallback_used
    assert [c["method"] for c in model.structured_calls] == ["function_calling", "json_schema"]


async def test_transport_errors_are_not_retried_with_another_method() -> None:
    request = httpx.Request("POST", "https://llm.test")
    rate_limited = openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )
    model = StubModel({"json_schema": rate_limited, "function_calling": _ok(Verdict(answer="x"))})

    with pytest.raises(openai.RateLimitError):
        await _client(model).structured([HumanMessage("q")], Verdict)

    assert len(model.structured_calls) == 1


async def test_both_methods_failing_raises() -> None:
    bad = {"raw": AIMessage(content="x"), "parsed": None, "parsing_error": ValueError("nope")}
    model = StubModel({"json_schema": bad, "function_calling": bad})

    with pytest.raises(LLMOutputError):
        await _client(model).structured([HumanMessage("q")], Verdict)


# ------------------------------------------------------------------ chat


async def test_chat_binds_tools_only_when_given() -> None:
    model = StubModel()
    tool = StructuredTool.from_function(func=lambda x: x, name="echo", description="Echo.")

    await _client(model).chat([HumanMessage("q")], tools=[tool])
    assert model.bound_tools == ["echo"]

    model.bound_tools = []
    reply = await _client(model).chat([HumanMessage("q")])
    assert reply.content == "hi"
    assert model.bound_tools == []


# ------------------------------------------------------------------ provider selection


def _settings(provider: str, **groups: Any) -> Settings:
    return Settings(llm=LLMSettings(provider=provider), **groups)  # type: ignore[arg-type]


def test_groq_client_uses_openai_compatible_endpoint() -> None:
    settings = _settings("groq", groq=GroqSettings(api_key=SecretStr("gsk"), model="llama-x"))

    client = build_llm_client(settings)

    model = client._model
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base == "https://api.groq.com/openai/v1"
    assert model.model_name == "llama-x"
    assert client.model_name == "groq:llama-x"
    assert client._method == "function_calling"  # provider default


def test_ollama_client_needs_no_credentials() -> None:
    settings = _settings("ollama", ollama=OllamaSettings(model="qwen2.5:3b"))

    client = build_llm_client(settings)

    assert client._model.openai_api_base == "http://localhost:11434/v1"  # type: ignore[attr-defined]
    assert client.model_name == "ollama:qwen2.5:3b"
    assert settings.llm_is_configured


def test_azure_client_from_settings() -> None:
    azure = AzureOpenAISettings(
        endpoint="https://res.openai.azure.com", api_key=SecretStr("k"), chat_deployment="gpt-prod"
    )
    settings = Settings(llm=LLMSettings(provider="azure_openai", max_retries=4), azure_openai=azure)

    client = build_llm_client(settings)

    model = client._model
    assert isinstance(model, AzureChatOpenAI)
    assert (model.deployment_name, model.max_retries) == ("gpt-prod", 4)
    assert client._method == "json_schema"


def test_structured_method_can_be_overridden() -> None:
    settings = Settings(
        llm=LLMSettings(provider="groq", structured_output="json_schema"),
        groq=GroqSettings(api_key=SecretStr("gsk")),
    )

    assert build_llm_client(settings)._method == "json_schema"


@pytest.mark.parametrize(
    ("provider", "message"),
    [("groq", "GROQ_API_KEY"), ("azure_openai", "AZURE_OPENAI_ENDPOINT")],
)
def test_unconfigured_provider_raises(provider: str, message: str) -> None:
    settings = _settings(provider)

    assert not settings.llm_is_configured
    with pytest.raises(LLMNotConfiguredError, match=message):
        build_llm_client(settings)
