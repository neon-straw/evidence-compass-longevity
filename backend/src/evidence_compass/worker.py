"""Серверный worker: берёт операции прямо из PostgreSQL (ADR 0008).

`FOR UPDATE SKIP LOCKED` без брокера: второй worker не возьмёт ту же операцию.
Запуск: `uv run python -m evidence_compass.worker`.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evidence_compass import (
    criteria,
    documents,
    extraction,
    extractor,
    operations,
    questions,
    search,
    selection,
    sources,
)
from evidence_compass.criteria import Resource
from evidence_compass.db import create_engine, create_sessionmaker
from evidence_compass.llm import LLM, Completion, llm_from_settings
from evidence_compass.retrieval import Retriever
from evidence_compass.settings import Settings
from evidence_compass.storage import Storage

log = logging.getLogger(__name__)


async def recover(db: AsyncSession) -> None:
    """После сбоя worker операция, взятая в работу, возвращается в очередь.

    Рассчитано на один worker установки (compose): при нескольких перезапуск одного вернул бы
    в очередь живую операцию другого. Подготовка критериев идемпотентна: повтор перезаписывает
    предложения того же черновика. Поиск продолжает только источники без исхода, извлечение —
    только работы без итога; запрошенные до сбоя пауза и остановка выполняются.
    """
    await db.execute(
        update(operations.OperationRecord)
        .where(operations.OperationRecord.status == "running")
        .values(status="queued")
    )
    stopping = await db.scalars(
        select(operations.OperationRecord).where(
            operations.OperationRecord.status == "terminate_requested"
        )
    )
    for operation in stopping:
        if operation.kind == "search":
            await search.stop_requested(db, operation)
    await extraction.recover(db)
    await documents.recover(db)
    await db.commit()


async def run_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm: LLM | None,
    settings: Settings,
    adapters: dict[Resource, sources.Source],
    retriever: Retriever,
    extractor: extraction.Extractor,
) -> bool:
    """Выполняет одну операцию или получение одного документа; False — очереди пусты.

    Получение документов не занимает общий слот и идёт между операциями.
    """
    if await _run_operation(sessionmaker, llm, settings, adapters, extractor):
        return True
    return await documents.fetch_next(sessionmaker, retriever, Storage(settings.documents_dir))


async def _run_operation(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm: LLM | None,
    settings: Settings,
    adapters: dict[Resource, sources.Source],
    extractor: extraction.Extractor,
) -> bool:
    async with sessionmaker() as db:
        operation = await db.scalar(
            select(operations.OperationRecord)
            .where(operations.OperationRecord.status == "queued")
            .order_by(operations.OperationRecord.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if operation is None:
            return False
        operation.status = "running"
        operation.started_at = datetime.now(UTC)
        await db.commit()
        operation_id = operation.id
        try:
            if operation.kind == "search":
                await search.execute(db, operation, adapters, settings, selection.absorb)
            elif operation.kind == "extraction":
                await extraction.execute(db, operation, extractor)
            else:
                await _prepare_criteria(db, operation, llm, settings)
        except Exception:
            # Непредвиденная ошибка не должна навсегда занять общий слот.
            await db.rollback()
            interrupted = await db.get(operations.OperationRecord, operation_id)
            assert interrupted is not None
            if interrupted.kind == "search":
                await search.fail(db, interrupted)
            elif interrupted.kind == "extraction":
                await extraction.fail(db, interrupted)
            else:
                await _finish(db, interrupted, None, 0, search.INTERNAL_ERROR)
            raise
        return True


async def _prepare_criteria(
    db: AsyncSession, operation: operations.OperationRecord, llm: LLM | None, settings: Settings
) -> None:
    question = await db.get(questions.QuestionVersionRecord, operation.input["question_version_id"])
    assert question is not None
    # Вызов модели идёт вне транзакции: соединение и строки не заняты на время ожидания.
    await db.commit()
    try:
        if llm is None:
            raise criteria.SuggestionFailed("подключение к модели не настроено", 0)
        completion, attempts = await criteria.suggest(llm, question.text, settings.llm_attempts)
    except criteria.SuggestionFailed as failure:
        # Не `waiting_llm`: у шага критериев нет «Продолжить», а ожидание заняло бы слот.
        # Отказ освобождает слот; подготовку повторяют с шага «Вопрос» или заполняют вручную.
        await _finish(db, operation, None, failure.attempts, failure.reason)
    else:
        await _finish(db, operation, completion, attempts, None)


async def _finish(
    db: AsyncSession,
    operation: operations.OperationRecord,
    completion: Completion[criteria.CriteriaSuggestion] | None,
    attempts: int,
    reason: str | None,
) -> None:
    record = await db.scalar(
        select(criteria.CriteriaDraftRecord)
        .where(criteria.CriteriaDraftRecord.study_id == operation.study_id)
        .with_for_update()
    )
    assert record is not None
    operation.attempts = attempts
    operation.finished_at = datetime.now(UTC)
    if completion is None:
        reason = reason or "ответ не получен"
        criteria.record_failure(record, reason)
        operation.status, operation.error = "failed", reason
    else:
        criteria.apply_suggestion(record, completion.value, operation.input["question_version_id"])
        operation.status = "completed"
        operation.model, operation.provider = completion.model, completion.provider
    await db.commit()


def retriever_from_settings(client: httpx.AsyncClient, settings: Settings) -> Retriever:
    return Retriever(
        client,
        settings.document_timeout.total_seconds(),
        settings.document_limit,
        settings.unpaywall_email,
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    llm = llm_from_settings(settings)
    if llm is None:
        log.warning(
            "Подключение к модели не настроено: подготовка критериев и извлечение "
            "завершатся отказом"
        )
    async with sessionmaker() as db:
        await recover(db)
    client = httpx.AsyncClient(headers={"User-Agent": "evidence-compass"})
    adapters = sources.adapters(client, settings.source_timeout.total_seconds())
    retriever = retriever_from_settings(client, settings)
    # Без подключения к модели работы честно завершаются ошибкой, их можно повторить позже.
    extract = (
        extractor.ModelExtractor(llm, settings.llm_attempts) if llm else extraction.NotConnected()
    )
    try:
        while True:
            try:
                worked = await run_once(sessionmaker, llm, settings, adapters, retriever, extract)
            except Exception:
                log.exception("Операция не выполнена")
                worked = False
            if not worked:
                await asyncio.sleep(settings.worker_poll_interval.total_seconds())
    finally:
        await client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
