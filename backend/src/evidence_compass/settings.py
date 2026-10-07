from datetime import timedelta
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # Пустая переменная — как незаданная: compose передаёт и новые, и прежние имена подключения.
    model_config = SettingsConfigDict(
        env_prefix="EC_", env_file=".env", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql+asyncpg://ec:ec@localhost:5433/evidence_compass"
    # Изменяющие команды принимаются со своего origin; здесь — дополнительные (JSON-список).
    extra_origins: list[str] = []
    session_lifetime: timedelta = timedelta(days=30)

    # Подключение к модели через OpenAI-совместимый API (ADR 0007), по умолчанию OpenRouter.
    # Без ключа и модели критерии заполняются вручную. Прежние `EC_OPENROUTER_*` тоже читаются.
    llm_base_url: str = Field(
        "https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("EC_LLM_BASE_URL", "EC_OPENROUTER_BASE_URL"),
    )
    llm_api_key: SecretStr | None = Field(
        None, validation_alias=AliasChoices("EC_LLM_API_KEY", "EC_OPENROUTER_API_KEY")
    )
    llm_model: str | None = Field(
        None, validation_alias=AliasChoices("EC_LLM_MODEL", "EC_OPENROUTER_MODEL")
    )
    # Только для OpenRouter: провайдеры по порядку через запятую, например `deepinfra,together`;
    # запрос идёт только к ним, без перехода к другим. Пусто — провайдера выбирает OpenRouter.
    openrouter_providers: Annotated[list[str], NoDecode] = []
    llm_timeout: timedelta = timedelta(minutes=2)
    # Ограниченный бюджет попыток на один вызов модели; модель при этом не меняется.
    llm_attempts: int = 3
    worker_poll_interval: timedelta = timedelta(seconds=2)

    # Поиск в PubMed и Europe PMC: ожидание ответа и число сохраняемых записей источника.
    source_timeout: timedelta = timedelta(seconds=30)
    search_limit: int = 200
    # Ограниченный повтор временного отказа источника (Europe PMC при нагрузке отвечает пустым
    # телом); исход — ошибка, если все попытки не удались.
    source_attempts: int = 3
    source_retry_delay: timedelta = timedelta(seconds=5)

    # Документы (#13): отдельное файловое хранилище версий; рабочее и тестовое не пересекаются.
    documents_dir: Path = Path("storage/documents")
    document_timeout: timedelta = timedelta(minutes=1)
    # Предел размера одного файла документа, байт.
    document_limit: int = 100 * 2**20
    # Unpaywall требует адрес для связи; без него открытый PDF по DOI не ищется.
    unpaywall_email: str | None = None

    @field_validator("openrouter_providers", mode="before")
    @classmethod
    def _split_providers(cls, value: object) -> object:
        if isinstance(value, str):
            return [name.strip() for name in value.split(",") if name.strip()]
        return value
