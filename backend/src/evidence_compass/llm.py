"""Тонкий адаптер модели через OpenAI-совместимый API (ADR 0007), по умолчанию OpenRouter.

Ровно одна модель без резервных, structured output по Pydantic-схеме. У OpenRouter маршрутизация
идёт только к провайдерам, поддерживающим все параметры, и, если они заданы, только
к выбранным провайдерам. Фактические модель и провайдер берутся из ответа.
"""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from evidence_compass.settings import Settings


class LLMUnavailable(Exception):
    """Модель не дала пригодного ответа: сеть, отказ сервиса или ответ вне схемы."""


class LLMInvalidResponse(LLMUnavailable):
    """Модель ответила, но ответ не прошёл проверку схемы."""


@dataclass(frozen=True)
class Completion[T: BaseModel]:
    value: T
    model: str
    provider: str | None


class LLM(Protocol):
    # Модель из настроек подключения; фактическая приходит в `Completion`.
    model: str

    async def complete[T: BaseModel](
        self, *, system: str, user: str, schema: type[T]
    ) -> Completion[T]: ...


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float,
        providers: tuple[str, ...] = (),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._providers = providers
        self._transport = transport

    async def complete[T: BaseModel](
        self, *, system: str, user: str, schema: type[T]
    ) -> Completion[T]:
        body: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
        }
        if _is_openrouter(self._base_url):
            # Без поддержки structured output провайдер не выбирается; смены модели нет.
            provider: dict[str, object] = {"require_parameters": True}
            if self._providers:
                # Только выбранные провайдеры по порядку, без перехода к остальным.
                provider |= {"order": list(self._providers), "allow_fallbacks": False}
            body["provider"] = provider
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    "/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.TimeoutException as error:
            raise LLMUnavailable("время ожидания ответа модели истекло") from error
        except httpx.HTTPError as error:
            raise LLMUnavailable(f"API модели недоступен ({type(error).__name__})") from error
        if response.status_code != 200:
            raise LLMUnavailable(f"API модели ответил {response.status_code}")
        try:
            data = response.json()
            if "error" in data:
                raise LLMUnavailable(f"API модели вернул ошибку: {data['error'].get('message')}")
            content = data["choices"][0]["message"]["content"]
            value = schema.model_validate_json(content)
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as error:
            raise LLMInvalidResponse("ответ модели не соответствует ожидаемой структуре") from error
        return Completion(value, data.get("model") or self.model, data.get("provider"))


def _is_openrouter(base_url: str) -> bool:
    # Поле `provider` понимает только OpenRouter; другой OpenAI-совместимый API может его отклонить.
    host = urlsplit(base_url).hostname or ""
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def llm_from_settings(settings: Settings) -> LLM | None:
    """None — подключение к модели не настроено: критерии заполняются вручную."""
    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    if not key or not settings.llm_model:
        return None
    return OpenAICompatibleLLM(
        api_key=key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout.total_seconds(),
        providers=tuple(settings.openrouter_providers),
    )
