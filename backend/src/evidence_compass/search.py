"""Запуск поиска по версии критериев и исход каждого источника (spec, «Отбор и состояния»; AC02).

Поиск — операция в общем слоте установки. Каждый запуск — новая строка, связанная со своей
версией критериев; прежние запуски не меняются. Исход источника фиксируется вместе с его
записями одной транзакцией, поэтому после сбоя worker продолжает только источники без исхода.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass import criteria, operations, questions, sources
from evidence_compass.criteria import CriteriaFields, CriteriaVersionRecord, Resource
from evidence_compass.db import Base
from evidence_compass.operations import Busy, OperationRecord
from evidence_compass.search_queries import query
from evidence_compass.settings import Settings

SearchStatus = Literal["not_started", "running", "completed", "stopped"]
SourceStatus = Literal["pending", "running", "hits", "empty", "error", "stopped"]
SearchAction = Literal["start", "stop", "retry"]
SOURCE_STATUSES = ("pending", "running", "hits", "empty", "error", "stopped")
UNFINISHED = ("pending", "running")

NOT_STARTED = "Сначала запустите поиск."
RUNNING = "Дождитесь завершения поиска."
STOPPED = "Поиск остановлен: повторите поиск."
ALL_FAILED = "Все ресурсы завершились ошибкой: повторите поиск или измените критерии."
FAILED = "Поиск прерван внутренней ошибкой: повторите поиск."
INTERNAL_ERROR = "внутренняя ошибка обработки"


class SearchRunRecord(Base):
    """Один запуск поиска по конкретной версии критериев. Повтор создаёт новую строку."""

    __tablename__ = "search_runs"
    __table_args__ = (UniqueConstraint("study_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    criteria_version_id: Mapped[int] = mapped_column(ForeignKey("criteria_versions.id"))
    # Ход и остановку запуска хранит операция; строка запуска не дублирует её состояние.
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id"), unique=True)
    number: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SearchSourceRecord(Base):
    """Исход одного источника в запуске: pending → hits / empty / error (или stopped)."""

    __tablename__ = "search_sources"
    __table_args__ = (
        UniqueConstraint("run_id", "resource"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{value}'" for value in SOURCE_STATUSES) + ")",
            name="status_known",
        ),
        CheckConstraint("resource IN ('pubmed', 'europe_pmc')", name="resource_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"))
    resource: Mapped[str] = mapped_column(String(16))
    # Фактический текст запроса — снимок на момент запуска.
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # Число записей, о котором сообщил источник; сохраняются не более первых `search_limit`.
    hit_count: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchRecordRecord(Base):
    """Запись выдачи источника как есть; объединение и решение о соответствии — `selection`."""

    __tablename__ = "search_records"
    __table_args__ = (UniqueConstraint("source_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("search_sources.id", ondelete="CASCADE"))
    # Порядок в выдаче источника, с нуля.
    position: Mapped[int]
    source_record_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None]
    doi: Mapped[str | None] = mapped_column(Text)
    pmid: Mapped[str | None] = mapped_column(String(32))
    pmcid: Mapped[str | None] = mapped_column(String(32))
    abstract: Mapped[str | None] = mapped_column(Text)
    publication_types: Mapped[list[str]] = mapped_column(JSONB, default=list)
    open_access: Mapped[bool | None]


@dataclass(frozen=True)
class SourceRun:
    resource: Resource
    query: str
    status: SourceStatus
    hit_count: int | None
    error: str | None
    returned_count: int | None = None


@dataclass(frozen=True)
class Run:
    status: SearchStatus
    criteria_version: int
    progress: float
    sources: list[SourceRun]
    allowed_actions: list[SearchAction]
    selection_blocked_reason: str | None


@dataclass(frozen=True)
class Refused:
    """Поиск нельзя запустить по подтверждённым критериям; сохранённое не изменилось."""

    message: str


def _status(operation: OperationRecord) -> SearchStatus:
    if operation.status in operations.ACTIVE:
        return "running"
    return "stopped" if operation.status == "terminated" else "completed"


async def _confirmed(db: AsyncSession, study_id: int) -> CriteriaVersionRecord | None:
    return await db.scalar(
        select(CriteriaVersionRecord)
        .where(CriteriaVersionRecord.study_id == study_id)
        .order_by(CriteriaVersionRecord.number.desc())
        .limit(1)
    )


async def latest_run(db: AsyncSession, study_id: int) -> SearchRunRecord | None:
    return await db.scalar(
        select(SearchRunRecord)
        .where(SearchRunRecord.study_id == study_id)
        .order_by(SearchRunRecord.number.desc())
        .limit(1)
    )


async def source_runs(db: AsyncSession, run_id: int) -> list[SearchSourceRecord]:
    rows = await db.scalars(
        select(SearchSourceRecord)
        .where(SearchSourceRecord.run_id == run_id)
        .order_by(SearchSourceRecord.id)
    )
    return list(rows)


def _selection_blocked(
    status: SearchStatus, operation: OperationRecord, outcomes: list[str]
) -> str | None:
    if operation.status == "failed":
        # Исходы ресурсов могли сохраниться, но выдача не перенесена в отбор.
        return FAILED
    match status:
        case "not_started":
            return NOT_STARTED
        case "running":
            return RUNNING
        case "stopped":
            return STOPPED
    return ALL_FAILED if all(outcome == "error" for outcome in outcomes) else None


async def _view(db: AsyncSession, version: CriteriaVersionRecord) -> Run:
    slot = await operations.active(db)
    run = await latest_run(db, version.study_id)
    if run is None or run.criteria_version_id != version.id:
        # Новая версия критериев ещё не искалась: показываются запросы, которые будут отправлены.
        fields = CriteriaFields.model_validate(version.fields)
        return Run(
            status="not_started",
            criteria_version=version.number,
            progress=0.0,
            sources=[
                SourceRun(resource, query(resource, fields), "pending", None, None)
                for resource in fields.resources
            ],
            allowed_actions=[] if slot else ["start"],
            selection_blocked_reason=NOT_STARTED,
        )
    operation = await db.get(OperationRecord, run.operation_id)
    assert operation is not None
    rows = await source_runs(db, run.id)
    counts = dict(
        (
            await db.execute(
                select(SearchRecordRecord.source_id, func.count(SearchRecordRecord.id))
                .join(SearchSourceRecord, SearchSourceRecord.id == SearchRecordRecord.source_id)
                .where(SearchSourceRecord.run_id == run.id)
                .group_by(SearchRecordRecord.source_id)
            )
        ).all()
    )
    status = _status(operation)
    if status == "running":
        # Запрошенная остановка ждёт безопасной границы: повторно её не предлагают.
        allowed: list[SearchAction] = ["stop"] if operation.status in ("queued", "running") else []
    else:
        allowed = [] if slot else ["retry"]
    finished = [row for row in rows if row.status not in UNFINISHED]
    return Run(
        status=status,
        criteria_version=version.number,
        progress=len(finished) / len(rows) if rows else 0.0,
        sources=[
            SourceRun(
                row.resource,
                row.query,
                row.status,
                row.hit_count,
                row.error,
                counts.get(row.id, 0) if row.status in ("hits", "empty") else None,
            )
            for row in rows
        ],
        allowed_actions=allowed,
        selection_blocked_reason=_selection_blocked(
            status, operation, [row.status for row in rows]
        ),
    )


async def get(db: AsyncSession, account_id: int) -> Run | None:
    """Последний запуск по последней подтверждённой версии; None — критерии не подтверждались."""
    study = await questions.find_study(db, account_id)
    version = await _confirmed(db, study.id) if study else None
    return await _view(db, version) if version else None


async def start(db: AsyncSession, account_id: int) -> Run | Busy | Refused | None:
    """Ставит новый запуск по последней подтверждённой версии критериев в общий слот."""
    study = await questions.find_study(db, account_id, lock=True)
    version = await _confirmed(db, study.id) if study else None
    if study is None or version is None:
        await db.rollback()
        return None
    if busy := await operations.busy(db):
        return busy
    fields = CriteriaFields.model_validate(version.fields)
    if found := criteria.problems(fields):
        await db.rollback()
        return Refused(" ".join(problem.message for problem in found))
    operation = OperationRecord(
        study_id=study.id,
        kind="search",
        status="queued",
        input={"criteria_version_id": version.id},
        created_at=datetime.now(UTC),
    )
    db.add(operation)
    number = await db.scalar(
        select(func.max(SearchRunRecord.number)).where(SearchRunRecord.study_id == study.id)
    )
    try:
        await db.flush()
    except IntegrityError:
        # Слот занят параллельным запуском: уникальный индекс операций.
        await db.rollback()
        return await operations.busy(db) or Busy("Слот операций занят.")
    run = SearchRunRecord(
        study_id=study.id,
        criteria_version_id=version.id,
        operation_id=operation.id,
        number=(number or 0) + 1,
        created_at=operation.created_at,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        SearchSourceRecord(run_id=run.id, resource=resource, query=query(resource, fields))
        for resource in fields.resources
    )
    await db.commit()
    return await _view(db, version)


async def stop(db: AsyncSession, account_id: int) -> Run | None:
    """Останавливает незавершённый запуск; уже полученные исходы источников сохраняются.

    Запуск в очереди останавливается сразу. Во время запроса к источнику остановка лишь
    запрашивается: worker завершит запрос и остановится на границе между источниками.
    """
    study = await questions.find_study(db, account_id)
    version = await _confirmed(db, study.id) if study else None
    if version is None:
        return None
    run = await latest_run(db, version.study_id)
    if run is not None:
        operation = await db.get(OperationRecord, run.operation_id, with_for_update=True)
        assert operation is not None
        if operation.status == "queued":
            await _terminate(db, operation, run.id)
        elif operation.status == "running":
            operation.status = "terminate_requested"
        await db.commit()
    return await _view(db, version)


async def _terminate(db: AsyncSession, operation: OperationRecord, run_id: int) -> None:
    for row in await source_runs(db, run_id):
        if row.status in UNFINISHED:
            row.status, row.finished_at = "stopped", datetime.now(UTC)
    operation.status, operation.finished_at = "terminated", datetime.now(UTC)


async def _run_of(db: AsyncSession, operation: OperationRecord) -> SearchRunRecord:
    run = await db.scalar(
        select(SearchRunRecord).where(SearchRunRecord.operation_id == operation.id)
    )
    assert run is not None
    return run


async def _ask(source: sources.Source, query: str, settings: Settings) -> sources.Found:
    """Запрос с ограниченным бюджетом попыток; повторяется только временный отказ."""
    for attempt in range(1, settings.source_attempts + 1):
        try:
            return await source.search(query, settings.search_limit)
        except sources.SourceFailed as failure:
            if not failure.transient or attempt == settings.source_attempts:
                raise sources.SourceFailed(str(failure)) from failure
        await asyncio.sleep(settings.source_retry_delay.total_seconds())
    raise AssertionError("бюджет попыток не может быть пустым")


# Перенос выдачи в отбор в транзакции завершения; поиск не зависит от модуля отбора.
Absorb = Callable[[AsyncSession, SearchRunRecord], Awaitable[None]]


async def execute(
    db: AsyncSession,
    operation: OperationRecord,
    adapters: dict[Resource, sources.Source],
    settings: Settings,
    absorb: Absorb,
) -> None:
    """Опрашивает источники по очереди; между источниками проверяет запрос остановки."""
    run = await _run_of(db, operation)
    while True:
        await db.refresh(operation, with_for_update=True)
        if operation.status == "terminate_requested":
            await _terminate(db, operation, run.id)
            await db.commit()
            return
        pending = [row for row in await source_runs(db, run.id) if row.status in UNFINISHED]
        if not pending:
            await _complete(db, operation, run, absorb)
            return
        source = pending[0]
        source.status = "running"
        # Запрос к источнику идёт вне транзакции: строка операции свободна для остановки.
        await db.commit()
        try:
            found = await _ask(adapters[source.resource], source.query, settings)
        except sources.SourceFailed as failure:
            source.status, source.error = "error", str(failure)
        else:
            source.status = "hits" if found.hit_count else "empty"
            source.hit_count = found.hit_count
            db.add_all(
                SearchRecordRecord(
                    source_id=source.id,
                    position=position,
                    source_record_id=record.source_id,
                    title=record.title,
                    authors=record.authors,
                    year=record.year,
                    doi=record.doi,
                    pmid=record.pmid,
                    pmcid=record.pmcid,
                    abstract=record.abstract,
                    publication_types=record.publication_types,
                    open_access=record.open_access,
                )
                for position, record in enumerate(found.records)
            )
        source.finished_at = datetime.now(UTC)
        await db.commit()


async def _complete(
    db: AsyncSession, operation: OperationRecord, run: SearchRunRecord, absorb: Absorb
) -> None:
    outcomes = [row.status for row in await source_runs(db, run.id)]
    operation.status, operation.finished_at = "completed", datetime.now(UTC)
    if not all(outcome == "error" for outcome in outcomes):
        # Найденное хотя бы одним источником (или успешная пустая выдача) открывает отбор.
        await absorb(db, run)
        study = await db.get(questions.StudyRecord, run.study_id, with_for_update=True)
        assert study is not None
        if study.reached_step in ("question", "criteria", "search"):
            study.reached_step = "selection"
    await db.commit()


async def fail(db: AsyncSession, operation: OperationRecord, reason: str = INTERNAL_ERROR) -> None:
    """Непредвиденная ошибка: источники без исхода получают ошибку, слот освобождается."""
    run = await _run_of(db, operation)
    for row in await source_runs(db, run.id):
        if row.status in UNFINISHED:
            row.status, row.error = "error", reason
            row.finished_at = datetime.now(UTC)
    operation.status, operation.error = "failed", reason
    operation.finished_at = datetime.now(UTC)
    await db.commit()


async def stop_requested(db: AsyncSession, operation: OperationRecord) -> None:
    """После сбоя worker запрошенная остановка выполняется, а не теряется."""
    await _terminate(db, operation, (await _run_of(db, operation)).id)
    await db.commit()
