from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI

from evidence_compass import sources
from evidence_compass.api import auth, extraction, health, reset, result, selection, study
from evidence_compass.db import create_engine, create_sessionmaker
from evidence_compass.llm import LLM, llm_from_settings
from evidence_compass.settings import Settings
from evidence_compass.storage import Storage

# Маршруты, доступные без входа. Всё остальное подключается к `protected`.
PUBLIC_ROUTERS = (health.router, auth.router)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLM | None = None,
    lookup_source: sources.Source | None = None,
) -> FastAPI:
    """`llm` и `lookup_source` подменяют модель и Europe PMC в тестах.

    Иначе подключение к модели строится из настроек (ADR 0007), а поиск записи для ручного
    добавления идёт в Europe PMC, который индексирует и PubMed, и препринты.
    """
    settings = settings or Settings()
    http = httpx.AsyncClient(headers={"User-Agent": "evidence-compass"})

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await http.aclose()

    app = FastAPI(
        title="Evidence Compass",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.llm = llm or llm_from_settings(settings)
    app.state.lookup_source = lookup_source or sources.EuropePMC(
        http, settings.source_timeout.total_seconds()
    )
    app.state.storage = Storage(settings.documents_dir)
    app.state.engine = create_engine(settings)
    app.state.sessionmaker = create_sessionmaker(app.state.engine)
    app.add_middleware(auth.OriginCheckMiddleware, extra_origins=settings.extra_origins)

    for router in PUBLIC_ROUTERS:
        app.include_router(router, prefix="/api")
    protected = APIRouter(prefix="/api", dependencies=[Depends(auth.current_account)])
    protected.include_router(study.router)
    protected.include_router(selection.router)
    protected.include_router(extraction.router)
    protected.include_router(result.router)
    protected.include_router(reset.router)  # временно
    app.include_router(protected)
    return app
