import asyncio
import tempfile
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from evidence_compass.api import create_app
from evidence_compass.db import Base, create_engine, create_sessionmaker
from evidence_compass.settings import Settings
from evidence_compass.sources import EuropePMC
from tests.fake_extractor import FakeExtractor
from tests.fake_llm import FakeLLM
from tests.fake_sources import FakeSources

ORIGIN = "http://test"


class TestEnvironment(BaseSettings):
    """Тесты используют только отдельный PostgreSQL `db-test` (ADR 0008)."""

    model_config = SettingsConfigDict(env_prefix="EC_TEST_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://ec:ec@localhost:5434/evidence_compass_test"


TEST_DATABASE_URL = TestEnvironment().database_url
# Хранилище документов тестов отдельно от рабочего (ADR 0008).
TEST_DOCUMENTS_DIR = Path(tempfile.mkdtemp(prefix="ec-test-documents-"))


def settings_for_tests(**overrides) -> Settings:
    # Повтор отказа источника в тестах без ожидания.
    defaults = {
        "database_url": TEST_DATABASE_URL,
        "source_retry_delay": timedelta(0),
        "documents_dir": TEST_DOCUMENTS_DIR,
    }
    return Settings(**{**defaults, **overrides})


@pytest.fixture(scope="session", autouse=True)
async def migrated_database() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_engine(settings_for_tests())
    async with create_sessionmaker(engine)() as session:
        yield session
    async with engine.begin() as connection:
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def sources() -> FakeSources:
    return FakeSources()


@pytest.fixture
async def source_http(sources) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP-клиент ресурсов, подменённый сохранёнными ответами `FakeSources`."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(sources.handle))
    yield http
    await http.aclose()


@pytest.fixture
def make_client(llm, source_http) -> Callable[..., httpx.AsyncClient]:
    def make(settings: Settings | None = None, *, model=llm) -> httpx.AsyncClient:
        settings = settings or settings_for_tests()
        lookup = EuropePMC(source_http, settings.source_timeout.total_seconds())
        app = create_app(settings, llm=model, lookup_source=lookup)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
        )

    return make


@pytest.fixture
async def client(make_client, db) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


@pytest.fixture
async def run_worker(llm, source_http, extractor) -> AsyncIterator[Callable[..., object]]:
    """Прогоняет worker до пустой очереди, как отдельный серверный процесс (ADR 0008)."""
    from evidence_compass import worker
    from evidence_compass.sources import adapters

    engine = create_engine(settings_for_tests())
    sessionmaker = create_sessionmaker(engine)

    async def run(settings: Settings | None = None) -> int:
        settings = settings or settings_for_tests()
        found = adapters(source_http, settings.source_timeout.total_seconds())
        retriever = worker.retriever_from_settings(source_http, settings)
        done = 0
        while await worker.run_once(sessionmaker, llm, settings, found, retriever, extractor):
            done += 1
        return done

    yield run
    await engine.dispose()
