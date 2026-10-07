"""Операция извлечения: жизненный цикл обработки выбранных работ (#14; spec «Отбор и состояния»).

Извлечение — операция в общем слоте установки. Запуск фиксирует неизменяемый снимок входов:
работы и версии их документов; каждая работа — элемент операции. Итог элемента сохраняется
одной транзакцией с его завершением, поэтому после сбоя worker продолжает только незавершённые
работы. Пауза и остановка подтверждаются на границе между работами: работа, которая уже
обрабатывается, завершается. Содержание обработки работы — шов `Extractor` (#15): он получает
разобранные документы снимка и возвращает итог с кандидатной ревизией карточки, которая
сохраняется той же транзакцией, что и итог.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, get_args

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass import cards, documents, operations, questions
from evidence_compass.criteria import CriteriaVersionRecord
from evidence_compass.db import Base
from evidence_compass.operations import Busy, OperationRecord
from evidence_compass.selection import PublicationRecord

Execution = Literal["queued", "running", "done", "failed", "skipped"]
Outcome = Literal["measures_extracted", "no_measures", "unresolved", "unavailable", "error"]
Status = Literal[
    "not_started",
    "queued",
    "running",
    "waiting_llm",
    "pause_requested",
    "paused",
    "terminate_requested",
    "terminated",
    "completed",
    "failed",
]
Action = Literal["start", "pause", "resume", "stop", "retry_failed"]
EXECUTIONS = get_args(Execution)
OUTCOMES = get_args(Outcome)
UNFINISHED = ("queued", "running")

INTERNAL_ERROR = "Внутренняя ошибка обработки."
NOT_CONNECTED = "Подключение к модели не настроено: показатели не извлекаются."
ALL_PROCESSED = "Все работы с готовым основным текстом уже обработаны."
CHANGED = "Документы изменились после загрузки экрана: обновите страницу и запустите снова."
NOT_STARTABLE = "Работа не готова к извлечению или уже обработана: обновите страницу."
KEY_REUSED = "Ключ запуска уже использован с другими работами или версиями документов."
NOTHING_FAILED = "Неудачных работ нет."
NOTHING_READY = "Документы неудачных работ не готовы: загрузите или подтвердите основной текст."

OUTCOME_LABELS = {
    "measures_extracted": "показатели извлечены",
    "no_measures": "без показателей",
    "unresolved": "требует решения",
    "unavailable": "документ недоступен",
    "error": "ошибка",
}


class ExtractionItemRecord(Base):
    """Работа в операции извлечения: снимок версий документов, выполнение и итог."""

    __tablename__ = "extraction_items"
    __table_args__ = (
        UniqueConstraint("operation_id", "publication_id"),
        CheckConstraint(
            "execution IN (" + ", ".join(f"'{value}'" for value in EXECUTIONS) + ")",
            name="execution_known",
        ),
        CheckConstraint(
            "outcome IN (" + ", ".join(f"'{value}'" for value in OUTCOMES) + ")",
            name="outcome_known",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id", ondelete="CASCADE"))
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    # Неизменяемый снимок: [{document_id, version}] пригодных документов работы при запуске.
    document_versions: Mapped[list[dict]] = mapped_column(JSONB)
    # Пробелы документов работы при запуске: сохраняются вместе с итогом.
    gaps: Mapped[list[str]] = mapped_column(JSONB, default=list)
    execution: Mapped[str] = mapped_column(String(16), default="queued")
    outcome: Mapped[str | None] = mapped_column(String(32))
    measure_count: Mapped[int | None]
    detail: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationEventRecord(Base):
    """Запись журнала операции: команды, подтверждения и итоги работ."""

    __tablename__ = "operation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operations.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    message: Mapped[str] = mapped_column(Text)


# ---------- Шов обработки работы ----------


@dataclass(frozen=True)
class SourceDocument:
    """Версия документа из снимка операции с разобранным содержимым."""

    document_id: int
    version: int
    version_id: int
    name: str
    role: documents.Role
    format: str
    parse_status: str
    content: dict | None


@dataclass(frozen=True)
class WorkInput:
    publication_id: int
    title: str
    question: str
    # Объекты исследования из версии критериев: {name, scientific_name, aliases}.
    objects: list[dict]
    criteria_version_id: int | None
    # Пригодные документы работы из снимка операции.
    documents: list[SourceDocument]


@dataclass(frozen=True)
class WorkResult:
    outcome: Outcome
    measure_count: int | None
    detail: str | None
    # Кандидатная ревизия карточки и фактические модель и провайдер, которые её дали.
    card: cards.Candidate | None = None
    model: str | None = None
    provider: str | None = None


class ModelUnavailable(Exception):
    """Модель не ответила в пределах бюджета повторов: операция ждёт явного продолжения."""


class Extractor(Protocol):
    async def extract(self, work: WorkInput) -> WorkResult: ...


class NotConnected:
    """Обработка до #15: работа честно завершается ошибкой, её можно повторить позже."""

    async def extract(self, work: WorkInput) -> WorkResult:
        return WorkResult("error", None, NOT_CONNECTED)


# ---------- Чтение ----------


@dataclass(frozen=True)
class WorkRun:
    publication_id: int
    label: str
    execution: Execution
    outcome: Outcome | None
    measure_count: int | None
    detail: str | None
    gaps: list[str]


@dataclass(frozen=True)
class Operation:
    status: Status
    progress: float
    works: list[WorkRun]
    allowed_actions: list[Action]


@dataclass(frozen=True)
class Preparation:
    """Документы выбранных работ вместе с операцией извлечения."""

    documents: documents.Extraction
    startable_publication_ids: list[int]
    start_blocked_reason: str | None
    operation: Operation


@dataclass(frozen=True)
class Refused:
    """Команда не выполнена; сохранённое не изменилось."""

    message: str
    target: str | None = None


@dataclass(frozen=True)
class Event:
    at: datetime
    message: str


def _now() -> datetime:
    return datetime.now(UTC)


def _log(db: AsyncSession, operation: OperationRecord, message: str) -> None:
    db.add(OperationEventRecord(operation_id=operation.id, at=_now(), message=message))


async def _operations(db: AsyncSession, study_id: int) -> list[OperationRecord]:
    rows = await db.scalars(
        select(OperationRecord)
        .where(OperationRecord.study_id == study_id, OperationRecord.kind == "extraction")
        .order_by(OperationRecord.id)
    )
    return list(rows)


async def _items(db: AsyncSession, operation_id: int) -> list[ExtractionItemRecord]:
    rows = await db.scalars(
        select(ExtractionItemRecord)
        .where(ExtractionItemRecord.operation_id == operation_id)
        .order_by(ExtractionItemRecord.id)
    )
    return list(rows)


async def latest_items(db: AsyncSession, study_id: int) -> dict[int, ExtractionItemRecord]:
    """Последний элемент каждой работы по всем операциям извлечения: итог работы переживает
    следующий запуск или повтор, в который она не вошла."""
    latest: dict[int, ExtractionItemRecord] = {}
    for operation in await _operations(db, study_id):
        for item in await _items(db, operation.id):
            latest[item.publication_id] = item
    return latest


async def _startable(
    db: AsyncSession, found: documents.Extraction, study_id: int
) -> tuple[list[int], dict[int, ExtractionItemRecord]]:
    latest = await latest_items(db, study_id)
    # Запускается работа без итога или пропущенная остановкой; готовая не повторяется,
    # неудачная — отдельной командой повтора.
    startable = [
        publication_id
        for publication_id in found.startable_publication_ids
        if publication_id not in latest or latest[publication_id].execution == "skipped"
    ]
    return startable, latest


def _allowed(
    operation: OperationRecord | None,
    slot: OperationRecord | None,
    startable: list[int],
    latest: dict[int, ExtractionItemRecord],
) -> list[Action]:
    if slot is not None:
        if operation is None or slot.id != operation.id:
            return []
        match operation.status:
            case "queued" | "running":
                return ["pause", "stop"]
            case "pause_requested":
                return ["stop"]
            case "paused" | "waiting_llm":
                return ["resume", "stop"]
        # Запрошенная остановка ждёт безопасной границы: повторно её не предлагают.
        return []
    allowed: list[Action] = ["start"] if startable else []
    if any(item.execution == "failed" for item in latest.values()):
        allowed.append("retry_failed")
    return allowed


async def _operation(
    db: AsyncSession,
    operation: OperationRecord | None,
    startable: list[int],
    latest: dict[int, ExtractionItemRecord],
) -> Operation:
    slot = await operations.active(db)
    allowed = _allowed(operation, slot, startable, latest)
    if operation is None:
        return Operation("not_started", 0.0, [], allowed)
    items = await _items(db, operation.id)
    finished = [item for item in items if item.execution not in UNFINISHED]
    works = []
    for item in sorted(latest.values(), key=lambda item: item.publication_id):
        publication = await db.get(PublicationRecord, item.publication_id)
        assert publication is not None
        if not publication.selected:
            continue
        works.append(
            WorkRun(
                publication_id=item.publication_id,
                label=documents.label(publication),
                execution=item.execution,
                outcome=item.outcome,
                measure_count=item.measure_count,
                detail=item.detail,
                gaps=item.gaps,
            )
        )
    return Operation(
        status=operation.status,
        progress=len(finished) / len(items) if items else 0.0,
        works=works,
        allowed_actions=allowed,
    )


async def get(
    db: AsyncSession, account_id: int, operation_id: int | None = None
) -> Preparation | None:
    """Документы и операция извлечения: указанная или последняя; None — исследования ещё нет."""
    study = await questions.find_study(db, account_id)
    found = await documents.get(db, account_id)
    if study is None or found is None:
        return None
    startable, latest = await _startable(db, found, study.id)
    blocked = found.start_blocked_reason
    if blocked is None and not startable:
        blocked = ALL_PROCESSED
    extractions = await _operations(db, study.id)
    operation = next(
        (item for item in extractions if item.id == operation_id),
        extractions[-1] if extractions else None,
    )
    return Preparation(
        documents=found,
        startable_publication_ids=startable,
        start_blocked_reason=blocked,
        operation=await _operation(db, operation, startable, latest),
    )


async def _current(db: AsyncSession, account_id: int, operation_id: int | None = None) -> Operation:
    found = await get(db, account_id, operation_id)
    assert found is not None
    return found.operation


async def log(db: AsyncSession, account_id: int) -> list[Event] | None:
    """Журнал всех операций извлечения исследования по времени."""
    study = await questions.find_study(db, account_id)
    if study is None:
        return None
    rows = await db.scalars(
        select(OperationEventRecord)
        .join(OperationRecord, OperationRecord.id == OperationEventRecord.operation_id)
        .where(OperationRecord.study_id == study.id, OperationRecord.kind == "extraction")
        .order_by(OperationEventRecord.id)
    )
    return [Event(row.at, row.message) for row in rows]


# ---------- Команды ----------


def _usable_versions(
    found: documents.Extraction, publication_ids: list[int]
) -> dict[int, list[tuple[int, int]]]:
    """(document_id, version) пригодных документов каждой работы."""
    versions: dict[int, list[tuple[int, int]]] = {id: [] for id in publication_ids}
    for row in found.documents:
        if row.publication_id in versions and documents.usable(row):
            assert row.version is not None
            versions[row.publication_id].append((row.id, row.version))
    return versions


def _snapshot(publication_ids: list[int], versions: set[tuple[int, int]]) -> dict:
    """Нормализованный снимок входов: сравнивается при повторе команды с тем же ключом."""
    return {
        "publication_ids": sorted(publication_ids),
        "document_versions": [
            {"document_id": document_id, "version": version}
            for document_id, version in sorted(versions)
        ],
    }


def _pairs(versions: dict[int, list[tuple[int, int]]]) -> set[tuple[int, int]]:
    return {pair for pairs in versions.values() for pair in pairs}


async def _enqueue(
    db: AsyncSession,
    study_id: int,
    found: documents.Extraction,
    versions: dict[int, list[tuple[int, int]]],
    key: str | None,
    message: str,
) -> OperationRecord | Busy:
    operation = OperationRecord(
        study_id=study_id,
        kind="extraction",
        status="queued",
        input=_snapshot(list(versions), _pairs(versions)),
        idempotency_key=key,
        created_at=_now(),
    )
    db.add(operation)
    try:
        await db.flush()
    except IntegrityError:
        # Слот занят параллельным запуском: уникальный индекс операций.
        await db.rollback()
        return await operations.busy(db) or Busy("Слот операций занят.")
    gaps = {work.publication_id: work.gaps for work in found.works}
    db.add_all(
        ExtractionItemRecord(
            operation_id=operation.id,
            publication_id=publication_id,
            document_versions=[
                {"document_id": document_id, "version": version}
                for document_id, version in sorted(pairs)
            ],
            gaps=gaps.get(publication_id, []),
        )
        for publication_id, pairs in sorted(versions.items())
    )
    _log(db, operation, message)
    await db.commit()
    return operation


async def start(
    db: AsyncSession,
    account_id: int,
    key: str,
    publication_ids: list[int],
    document_versions: list[tuple[int, int]],
) -> Operation | Busy | Refused | None:
    """Ставит обработку выбранных работ по снимку их документов в общий слот.

    Повтор с тем же ключом и тем же входом возвращает ту же операцию; тот же ключ с другим
    входом — отказ. Снимок должен совпадать с текущими пригодными версиями документов.
    """
    study = await questions.find_study(db, account_id, lock=True)
    if study is None:
        await db.rollback()
        return None
    requested = _snapshot(list(set(publication_ids)), set(document_versions))
    earlier = await db.scalar(select(OperationRecord).where(OperationRecord.idempotency_key == key))
    if earlier is not None:
        if earlier.study_id != study.id or earlier.input != requested:
            await db.rollback()
            return Refused(KEY_REUSED, "idempotency_key")
        replayed = earlier.id
        await db.rollback()
        return await _current(db, account_id, replayed)
    if busy := await operations.busy(db):
        return busy
    found = await documents.get(db, account_id)
    assert found is not None
    startable, _ = await _startable(db, found, study.id)
    ids = requested["publication_ids"]
    if not set(ids) <= set(startable):
        await db.rollback()
        return Refused(NOT_STARTABLE, "publication_ids")
    versions = _usable_versions(found, ids)
    if _snapshot(ids, _pairs(versions)) != requested:
        await db.rollback()
        return Refused(CHANGED, "document_versions")
    enqueued = await _enqueue(
        db, study.id, found, versions, key, f"Запуск извлечения: {_works(len(ids))}."
    )
    if isinstance(enqueued, Busy):
        return enqueued
    return await _current(db, account_id)


async def retry_failed(db: AsyncSession, account_id: int) -> Operation | Busy | Refused | None:
    """Новая операция только для неудачных работ по текущим версиям их документов.

    Готовые итоги других работ не затрагиваются.
    """
    study = await questions.find_study(db, account_id, lock=True)
    if study is None:
        await db.rollback()
        return None
    if busy := await operations.busy(db):
        return busy
    latest = await latest_items(db, study.id)
    failed = [item.publication_id for item in latest.values() if item.execution == "failed"]
    if not failed:
        await db.rollback()
        return Refused(NOTHING_FAILED)
    found = await documents.get(db, account_id)
    assert found is not None
    ready = [work.publication_id for work in found.works if work.state == "ready"]
    ids = [publication_id for publication_id in failed if publication_id in ready]
    if not ids:
        await db.rollback()
        return Refused(NOTHING_READY)
    enqueued = await _enqueue(
        db,
        study.id,
        found,
        _usable_versions(found, ids),
        None,
        f"Повтор неудачных работ: {_works(len(ids))}.",
    )
    if isinstance(enqueued, Busy):
        return enqueued
    return await _current(db, account_id)


async def _latest_operation(db: AsyncSession, account_id: int) -> OperationRecord | None:
    """Последняя операция извлечения под блокировкой строки."""
    study = await questions.find_study(db, account_id)
    if study is None:
        return None
    extractions = await _operations(db, study.id)
    if not extractions:
        return None
    operation = extractions[-1]
    # Статус читается из заблокированной строки: worker мог изменить его после выборки.
    await db.refresh(operation, with_for_update=True)
    return operation


async def pause(db: AsyncSession, account_id: int) -> Operation | None:
    """Операция в очереди встаёт на паузу сразу; выполняющаяся — после текущей работы."""
    operation = await _latest_operation(db, account_id)
    if operation is None:
        return None
    if operation.status == "queued":
        _pause(db, operation)
    elif operation.status == "running":
        operation.status = "pause_requested"
        _log(db, operation, "Запрошена пауза: текущая работа завершается.")
    await db.commit()
    return await _current(db, account_id)


async def resume(db: AsyncSession, account_id: int) -> Operation | None:
    """Продолжение после паузы или ожидания модели: модель не меняется."""
    operation = await _latest_operation(db, account_id)
    if operation is None:
        return None
    if operation.status in ("paused", "waiting_llm"):
        operation.status = "queued"
        _log(db, operation, "Продолжение.")
    await db.commit()
    return await _current(db, account_id)


async def stop(db: AsyncSession, account_id: int) -> Operation | None:
    """Остановка: без выполняющейся работы — сразу, иначе после её завершения.

    Запрос остановки не выдаётся за остановку: работа, уже переданная в обработку, завершается.
    """
    operation = await _latest_operation(db, account_id)
    if operation is None:
        return None
    if operation.status in ("queued", "paused", "waiting_llm"):
        await _terminate(db, operation)
    elif operation.status in ("running", "pause_requested"):
        operation.status = "terminate_requested"
        _log(db, operation, "Запрошена остановка: текущая работа завершается.")
    await db.commit()
    return await _current(db, account_id)


def _works(n: int) -> str:
    return f"{n} {documents.plural(n, ('работа', 'работы', 'работ'))}"


def _pause(db: AsyncSession, operation: OperationRecord) -> None:
    operation.status = "paused"
    _log(db, operation, "Пауза подтверждена.")


async def _terminate(db: AsyncSession, operation: OperationRecord) -> None:
    skipped = 0
    for item in await _items(db, operation.id):
        if item.execution in UNFINISHED:
            item.execution, item.finished_at = "skipped", _now()
            skipped += 1
    operation.status, operation.finished_at = "terminated", _now()
    _log(db, operation, f"Извлечение остановлено; не обработано: {_works(skipped)}.")


# ---------- Выполнение (worker) ----------


async def execute(db: AsyncSession, operation: OperationRecord, extractor: Extractor) -> None:
    """Обрабатывает работы по очереди; между работами подтверждает паузу и остановку."""
    while True:
        await db.refresh(operation, with_for_update=True)
        if operation.status == "terminate_requested":
            await _terminate(db, operation)
            await db.commit()
            return
        if operation.status == "pause_requested":
            _pause(db, operation)
            await db.commit()
            return
        items = await _items(db, operation.id)
        item = next((item for item in items if item.execution == "queued"), None)
        if item is None:
            operation.status, operation.finished_at = "completed", _now()
            _log(db, operation, _completed(items))
            await db.commit()
            return
        item.execution = "running"
        work = await _work(db, operation, item)
        # Обработка идёт вне транзакции: строка операции свободна для паузы и остановки.
        await db.commit()
        try:
            result = await extractor.extract(work)
        except ModelUnavailable as failure:
            await _wait_for_model(db, operation, item, str(failure))
            if operation.status == "waiting_llm":
                return
            continue
        except Exception:
            # Ошибка одной работы не мешает остальным.
            result = WorkResult("error", None, INTERNAL_ERROR)
        await _save(db, operation, item, work, result)


async def _work(
    db: AsyncSession, operation: OperationRecord, item: ExtractionItemRecord
) -> WorkInput:
    """Вход обработки работы: снимок документов и действующая версия критериев.

    Критерии не меняются, пока операция занимает общий слот.
    """
    publication = await db.get(PublicationRecord, item.publication_id)
    assert publication is not None
    criteria = await db.scalar(
        select(CriteriaVersionRecord)
        .where(CriteriaVersionRecord.study_id == operation.study_id)
        .order_by(CriteriaVersionRecord.number.desc())
        .limit(1)
    )
    question = (
        await db.get(questions.QuestionVersionRecord, criteria.question_version_id)
        if criteria
        else None
    )
    sources = []
    for entry in item.document_versions:
        document = await db.get(documents.DocumentRecord, entry["document_id"])
        version = await db.scalar(
            select(documents.DocumentVersionRecord).where(
                documents.DocumentVersionRecord.document_id == entry["document_id"],
                documents.DocumentVersionRecord.number == entry["version"],
            )
        )
        assert document is not None and version is not None
        sources.append(
            SourceDocument(
                document_id=document.id,
                version=version.number,
                version_id=version.id,
                name=document.name if document.role == "main" else version.file_name,
                role=document.role,
                format=version.format,
                parse_status=version.parse_status,
                content=version.parsed,
            )
        )
    return WorkInput(
        publication_id=publication.id,
        title=publication.title,
        question=question.text if question else "",
        objects=criteria.fields.get("objects", []) if criteria else [],
        criteria_version_id=criteria.id if criteria else None,
        documents=sources,
    )


def _completed(items: list[ExtractionItemRecord]) -> str:
    """Завершение попытки с итогами работ: `completed` не означает, что показатели получены."""
    n = len(items)
    counts = [
        f"{OUTCOME_LABELS[outcome]} — {sum(item.outcome == outcome for item in items)}"
        for outcome in OUTCOMES
        if any(item.outcome == outcome for item in items)
    ]
    works = documents.plural(n, ("работы", "работ", "работ"))
    return f"Извлечение завершено для {n} {works}: {', '.join(counts)}."


async def _wait_for_model(
    db: AsyncSession, operation: OperationRecord, item: ExtractionItemRecord, reason: str
) -> None:
    """Работа возвращается в очередь; операция ждёт явного продолжения без смены модели.

    Пауза или остановка, запрошенные во время вызова модели, подтверждаются, а не теряются.
    """
    await db.refresh(operation, with_for_update=True)
    item.execution = "queued"
    if operation.status == "running":
        operation.status = "waiting_llm"
        _log(db, operation, f"Модель не ответила: {reason} Продолжите явно.")
    else:
        _log(db, operation, f"Модель не ответила: {reason}")
    await db.commit()


async def _save(
    db: AsyncSession,
    operation: OperationRecord,
    item: ExtractionItemRecord,
    work: WorkInput,
    result: WorkResult,
) -> None:
    """Итог работы, её кандидатная ревизия карточки и завершение — одна транзакция: после
    сбоя работа не повторяется и ревизия не дублируется. Ошибка ревизию не пишет."""
    if result.card is not None:
        assert result.model is not None
        await cards.add(
            db,
            publication_id=item.publication_id,
            number=await cards.next_number(db, item.publication_id),
            item_id=item.id,
            criteria_version_id=work.criteria_version_id,
            document_versions=item.document_versions,
            candidate=result.card,
            model=result.model,
            provider=result.provider,
        )
        operation.model, operation.provider = result.model, result.provider
    item.execution = "failed" if result.outcome == "error" else "done"
    item.outcome, item.measure_count, item.detail = (
        result.outcome,
        result.measure_count,
        result.detail,
    )
    item.finished_at = _now()
    if item.execution == "done":
        # Готовый итог открывает шаг «Результат»; назад шаг не откатывается.
        study = await db.get(questions.StudyRecord, operation.study_id)
        assert study is not None
        study.reached_step = "result"
    publication = await db.get(PublicationRecord, item.publication_id)
    assert publication is not None
    # Название с точкой в конце не даёт «….: ошибка».
    text = f"{documents.label(publication).rstrip('.')}: {OUTCOME_LABELS[result.outcome]}"
    _log(db, operation, f"{text}. {result.detail}" if result.detail else f"{text}.")
    await db.commit()


async def fail(db: AsyncSession, operation: OperationRecord, reason: str = INTERNAL_ERROR) -> None:
    """Непредвиденная ошибка операции: слот освобождается, готовые итоги сохраняются."""
    for item in await _items(db, operation.id):
        if item.execution == "running":
            item.execution, item.outcome, item.detail = "failed", "error", reason
            item.finished_at = _now()
        elif item.execution == "queued":
            item.execution, item.finished_at = "skipped", _now()
    operation.status, operation.error = "failed", reason
    operation.finished_at = _now()
    _log(db, operation, f"Сбой операции: {reason}")
    await db.commit()


async def recover(db: AsyncSession) -> None:
    """После сбоя worker прерванная работа возвращается в очередь, запрошенные пауза и
    остановка выполняются. Готовые итоги не повторяются: они сохранены с завершением работы."""
    running = await db.scalars(
        select(ExtractionItemRecord).where(ExtractionItemRecord.execution == "running")
    )
    for item in running:
        item.execution = "queued"
    requested = await db.scalars(
        select(OperationRecord).where(
            OperationRecord.kind == "extraction",
            OperationRecord.status.in_(("pause_requested", "terminate_requested")),
        )
    )
    for operation in list(requested):
        if operation.status == "terminate_requested":
            await _terminate(db, operation)
        else:
            _pause(db, operation)
