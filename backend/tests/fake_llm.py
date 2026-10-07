"""Подменённая модель: контролируемые ответы вместо OpenRouter (spec, «Основная граница: API»)."""

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from evidence_compass.llm import Completion, LLMInvalidResponse, LLMUnavailable

ACTUAL_MODEL = "provider/actual-model-2026-09"
ACTUAL_PROVIDER = "Fake Provider"

# Legacy-предложение с отдельной группой видов: старые критерии остаются поддержаны.
PROPOSAL = {
    "objects": [
        {"name": "Голый землекоп", "scientific_name": "Heterocephalus glaber", "aliases": []},
        {"name": "Гренландская акула", "scientific_name": "Somniosus microcephalus", "aliases": []},
    ],
    "search_criteria": [
        {
            "label": "Виды животных",
            "terms": ["Heterocephalus glaber", "Somniosus microcephalus"],
        },
        {"label": "Продолжительность жизни", "terms": ["lifespan", "longevity"]},
    ],
}


@dataclass
class Call:
    system: str
    user: str
    schema: type[BaseModel]


@dataclass
class FakeLLM:
    """Отвечает `PROPOSAL`, пока тест не задал другой ответ или отказ.

    `replies` расходуются по одному на вызов; элемент-исключение имитирует отказ модели.
    """

    model: str = "test/model"
    replies: list[dict | Exception] = field(default_factory=list)
    default: dict | Callable[[], dict] = field(default_factory=lambda: PROPOSAL)
    calls: list[Call] = field(default_factory=list)

    async def complete[T: BaseModel](self, *, system: str, user: str, schema: type[T]):
        self.calls.append(Call(system, user, schema))
        reply = self.replies.pop(0) if self.replies else self.default
        if isinstance(reply, Exception):
            raise reply
        payload = reply() if callable(reply) else reply
        return Completion(schema.model_validate(payload), ACTUAL_MODEL, ACTUAL_PROVIDER)


def unavailable(reason: str = "OpenRouter ответил 502") -> LLMUnavailable:
    return LLMUnavailable(reason)


def invalid() -> LLMInvalidResponse:
    return LLMInvalidResponse("ответ модели не соответствует ожидаемой структуре")
