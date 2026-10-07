"""Адаптер OpenAI-совместимого API (ADR 0007): одна модель без резервных, structured output.

Граница — HTTP-запрос к API модели, подменённый `httpx.MockTransport`.
"""

import json

import httpx
import pytest

from evidence_compass.criteria import CriteriaSuggestion
from evidence_compass.llm import LLMUnavailable, OpenAICompatibleLLM
from evidence_compass.settings import Settings
from tests.fake_llm import PROPOSAL

MODEL = "anthropic/claude-sonnet-5"


def adapter(
    handler, providers: tuple[str, ...] = (), base_url: str = "https://openrouter.ai/api/v1"
) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        api_key="sk-test",
        model=MODEL,
        base_url=base_url,
        timeout=5,
        providers=providers,
        transport=httpx.MockTransport(handler),
    )


def reply(content: str, **extra) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": content}}], **extra}
    )


async def test_request_pins_one_model_and_requires_structured_output():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply(
            json.dumps(PROPOSAL), model="anthropic/claude-sonnet-5-20260901", provider="Anthropic"
        )

    completion = await adapter(handler).complete(
        system="system", user="question", schema=CriteriaSuggestion
    )

    body = json.loads(sent[0].content)
    assert sent[0].url == "https://openrouter.ai/api/v1/chat/completions"
    assert sent[0].headers["authorization"] == "Bearer sk-test"
    assert body["model"] == MODEL
    assert "models" not in body  # без списка резервных моделей
    assert body["provider"] == {"require_parameters": True}
    schema = body["response_format"]["json_schema"]
    assert body["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert completion.value == CriteriaSuggestion.model_validate(PROPOSAL)
    # Фактические модель и провайдер — из ответа.
    assert completion.model == "anthropic/claude-sonnet-5-20260901"
    assert completion.provider == "Anthropic"


async def test_chosen_providers_are_used_in_order_without_fallback():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply(json.dumps(PROPOSAL), provider="DeepInfra")

    await adapter(handler, ("deepinfra", "together")).complete(
        system="s", user="u", schema=CriteriaSuggestion
    )

    assert json.loads(sent[0].content)["provider"] == {
        "require_parameters": True,
        "order": ["deepinfra", "together"],
        "allow_fallbacks": False,
    }


async def test_other_openai_compatible_api_gets_no_openrouter_routing():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return reply(json.dumps(PROPOSAL))

    completion = await adapter(
        handler, ("deepinfra",), base_url="http://localhost:8000/v1"
    ).complete(system="s", user="u", schema=CriteriaSuggestion)

    body = json.loads(sent[0].content)
    assert sent[0].url == "http://localhost:8000/v1/chat/completions"
    assert "provider" not in body
    assert body["response_format"]["type"] == "json_schema"
    assert completion.model == MODEL
    assert completion.provider is None


def test_connection_is_read_from_llm_settings(monkeypatch):
    monkeypatch.setenv("EC_LLM_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("EC_LLM_API_KEY", "sk-local")
    monkeypatch.setenv("EC_LLM_MODEL", "qwen3")
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "http://localhost:8000/v1"
    assert settings.llm_api_key.get_secret_value() == "sk-local"
    assert settings.llm_model == "qwen3"


def test_former_openrouter_names_still_configure_the_connection(monkeypatch):
    for name in ("EC_LLM_BASE_URL", "EC_LLM_API_KEY", "EC_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EC_OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("EC_OPENROUTER_MODEL", MODEL)
    settings = Settings(_env_file=None)
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_api_key.get_secret_value() == "sk-or"
    assert settings.llm_model == MODEL


@pytest.mark.parametrize(
    ("value", "providers"),
    [("", []), ("deepinfra", ["deepinfra"]), (" deepinfra , together,", ["deepinfra", "together"])],
)
def test_providers_setting_is_a_comma_separated_list(monkeypatch, value, providers):
    monkeypatch.setenv("EC_OPENROUTER_PROVIDERS", value)
    assert Settings(_env_file=None).openrouter_providers == providers


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(502, json={"error": {"message": "upstream"}}), "API модели ответил 502"),
        (httpx.Response(200, json={"error": {"message": "no endpoints"}}), "no endpoints"),
        (reply('{"objects": []}'), "не соответствует"),
        (reply("not json"), "не соответствует"),
    ],
)
async def test_unusable_answers_are_reported_as_unavailable(response, reason):
    with pytest.raises(LLMUnavailable, match=reason):
        await adapter(lambda _: response).complete(system="s", user="u", schema=CriteriaSuggestion)


async def test_network_failure_is_reported_as_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMUnavailable, match="API модели недоступен"):
        await adapter(handler).complete(system="s", user="u", schema=CriteriaSuggestion)
