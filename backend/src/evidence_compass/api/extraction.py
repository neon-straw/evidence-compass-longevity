"""Контракт групп API «Документы» и «Обработка» (ADR 0003, 0006).

Документы — #13, жизненный цикл операции извлечения — #14.
"""

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from evidence_compass import documents, extraction, operations
from evidence_compass.api.auth import CurrentAccount, Db
from evidence_compass.api.study import OperationInProgress
from evidence_compass.criteria import Problem
from evidence_compass.storage import Storage

router = APIRouter()

DocumentRole = Literal["main", "supplement"]
# Один текстовый статус документа (shape §3.5).
# `not_parsed` — приложение нейтрального формата (изображение, код): не разбирается и не пробел.
DocumentStatus = Literal["fetching", "ready", "not_found", "needs_review", "error", "not_parsed"]
DocumentAction = Literal["retry_fetch", "upload", "confirm_identity"]
# Раздельные статусы документа (spec «Отбор и состояния»): последняя попытка получения,
# идентичность и разбор текущей версии. Без версии идентичности и разбора нет.
FetchStatus = Literal["pending", "fetching", "available", "unavailable", "error"]
IdentityStatus = Literal["pending", "confirmed", "needs_review", "mismatch"]
ParseStatus = Literal["pending", "parsing", "parsed", "partial", "unsupported", "error"]
Acquisition = Literal["fetched", "uploaded"]

# Выполнение операции (spec «Отбор и состояния»): пауза и остановка подтверждаются на безопасной
# границе, поэтому запрос и подтверждённое состояние различаются.
OperationStatus = Literal[
    "not_started",
    "queued",
    "running",
    # Модель не ответила в пределах бюджета повторов: продолжение только явное, без смены модели.
    "waiting_llm",
    "pause_requested",
    "paused",
    "terminate_requested",
    "terminated",
    "completed",
    # Общий сбой операции: последняя успешная ревизия остаётся доступной.
    "failed",
]
OperationAction = Literal["start", "pause", "resume", "stop", "retry_failed"]
WorkExecution = Literal["queued", "running", "done", "failed", "skipped"]
# Итог работы отдельно от выполнения: частичные показатели и пробелы не сводятся к одному исходу.
WorkOutcome = extraction.Outcome


class DocumentRow(BaseModel):
    id: str
    name: str
    publication_id: str
    # Вторая строка ячейки «Документ»: связанная работа.
    publication_label: str
    role: DocumentRole
    # Номер текущей версии; None — версии ещё нет.
    version: int | None
    status: DocumentStatus
    # Пояснение к статусу: что именно не получено или что проверить.
    status_detail: str | None
    # Ссылка на PDF, найденная Unpaywall, пока версии нет: файл не загрузился автоматически
    # (например, PMC отдаёт его только в браузере) — открыть в новой вкладке и загрузить вручную.
    source_url: str | None
    allowed_actions: list[DocumentAction]
    # None — документ не получался: приложение, добавленное пользователем.
    fetch_status: FetchStatus | None
    identity_status: IdentityStatus | None
    parse_status: ParseStatus | None
    # SHA-256 и способ получения текущей версии.
    sha256: str | None
    acquisition: Acquisition | None


# Состояние работы до запуска по её основному тексту (shape §3.5).
WorkState = Literal["fetching", "ready", "no_full_text", "needs_review"]
WorkAction = Literal["upload_supplement"]


class DocumentWork(BaseModel):
    """Строка работы над её документами; документы — в `Extraction.documents`."""

    publication_id: str
    label: str
    document_count: int
    state: WorkState
    # Пробелы документов работы, например «1 приложение с таблицами не прочитано».
    gaps: list[str]
    allowed_actions: list[WorkAction]


class WorkRun(BaseModel):
    publication_id: str
    label: str
    execution: WorkExecution
    outcome: WorkOutcome | None
    measure_count: int | None
    detail: str | None
    # Документы работы, которые не вошли в обработку: пробелы сохраняются вместе с показателями.
    gaps: list[str]


class Operation(BaseModel):
    status: OperationStatus
    # Доля завершённых работ, 0–1.
    progress: float
    works: list[WorkRun]
    allowed_actions: list[OperationAction]


class Extraction(BaseModel):
    documents: list[DocumentRow]
    # Выбранные работы в порядке показа; таблица группирует документы по ним.
    works: list[DocumentWork]
    # Работы, которые можно запустить сейчас: основной текст готов, результата ещё нет.
    startable_publication_ids: list[str]
    operation: Operation
    # Причина, по которой запуск недоступен; None — доступен.
    start_blocked_reason: str | None


class DocumentVersionRef(BaseModel):
    document_id: str
    version: int


class StartExtraction(BaseModel):
    """Снимок входов операции: работы (до восьми) и версии их документов."""

    publication_ids: list[str] = Field(min_length=1, max_length=8)
    document_versions: list[DocumentVersionRef]


class IdentityDecision(BaseModel):
    """Подтверждение, что файл относится к выбранной работе."""

    belongs_to_publication: bool


class LogEvent(BaseModel):
    at: str
    message: str


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


FileStorage = Annotated[Storage, Depends(get_storage)]
REFUSED = {409: {"model": Problem | OperationInProgress}}


def _document(row: documents.Row) -> DocumentRow:
    return DocumentRow(
        id=str(row.id),
        name=row.name,
        publication_id=str(row.publication_id),
        publication_label=row.publication_label,
        role=row.role,
        version=row.version,
        status=row.status,
        status_detail=row.status_detail,
        source_url=row.source_url,
        allowed_actions=row.allowed_actions,
        fetch_status=row.fetch_status,
        identity_status=row.identity_status,
        parse_status=row.parse_status,
        sha256=row.sha256,
        acquisition=row.acquisition,
    )


def _refused(result: operations.Busy | documents.Refused, document_id: str) -> JSONResponse:
    if isinstance(result, operations.Busy):
        body = OperationInProgress(message=result.message).model_dump()
    else:
        body = Problem(target=document_id, message=result.message).model_dump()
    return JSONResponse(body, status_code=status.HTTP_409_CONFLICT)


def _unknown() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")


def _id(document_id: str) -> int:
    if not document_id.isdigit():
        raise _unknown()
    return int(document_id)


def _answer(result: documents.Row | operations.Busy | documents.Refused, document_id: str):
    if isinstance(result, documents.Row):
        return _document(result)
    return _refused(result, document_id)


@router.get("/extraction", operation_id="getExtraction")
async def get_extraction(account: CurrentAccount, db: Db) -> Extraction:
    """Документы выбранных работ; работа без полного текста остаётся в таблице (AC04)."""
    found = await extraction.get(db, account.id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Исследование ещё не начато")
    return Extraction(
        documents=[_document(row) for row in found.documents.documents],
        works=[
            DocumentWork(
                publication_id=str(work.publication_id),
                label=work.label,
                document_count=work.document_count,
                state=work.state,
                gaps=work.gaps,
                allowed_actions=work.allowed_actions,
            )
            for work in found.documents.works
        ],
        startable_publication_ids=[str(item) for item in found.startable_publication_ids],
        operation=_operation(found.operation),
        start_blocked_reason=found.start_blocked_reason,
    )


def _operation(found: extraction.Operation) -> Operation:
    return Operation(
        status=found.status,
        progress=found.progress,
        works=[
            WorkRun(
                publication_id=str(work.publication_id),
                label=work.label,
                execution=work.execution,
                outcome=work.outcome,
                measure_count=work.measure_count,
                detail=work.detail,
                gaps=work.gaps,
            )
            for work in found.works
        ],
        allowed_actions=found.allowed_actions,
    )


@router.post(
    "/documents/{document_id}/retry-fetch", operation_id="retryDocumentFetch", responses=REFUSED
)
async def retry_document_fetch(document_id: str, account: CurrentAccount, db: Db) -> DocumentRow:
    """Новая попытка получения; успешная создаёт новую версию, прежние сохраняются."""
    try:
        result = await documents.retry_fetch(db, account.id, _id(document_id))
    except documents.NotFound:
        raise _unknown() from None
    return _answer(result, document_id)


@router.post(
    "/documents/{document_id}/upload",
    operation_id="uploadDocument",
    responses={**REFUSED, 413: {"model": Problem}},
)
async def upload_document(
    document_id: str,
    file: Annotated[UploadFile, File()],
    account: CurrentAccount,
    db: Db,
    storage: FileStorage,
    request: Request,
) -> DocumentRow:
    """Ручная загрузка создаёт новую версию документа."""
    data = await _read(request, file)
    if isinstance(data, int):
        return _too_large(document_id, data)
    try:
        result = await documents.upload(
            db, account.id, storage, _id(document_id), file.filename or "upload", data
        )
    except documents.NotFound:
        raise _unknown() from None
    return _answer(result, document_id)


async def _read(request: Request, file: UploadFile) -> bytes | int:
    """Файл целиком либо предел размера, если файл больше."""
    limit = request.app.state.settings.document_limit
    data = await file.read(limit + 1)
    return limit if len(data) > limit else data


def _too_large(target: str, limit: int) -> JSONResponse:
    body = Problem(target=target, message=f"Файл больше {limit // 2**20} МБ.")
    return JSONResponse(body.model_dump(), status_code=status.HTTP_413_CONTENT_TOO_LARGE)


@router.post(
    "/publications/{publication_id}/supplements",
    operation_id="addSupplement",
    responses={**REFUSED, 413: {"model": Problem}},
)
async def add_supplement(
    publication_id: str,
    file: Annotated[UploadFile, File()],
    account: CurrentAccount,
    db: Db,
    storage: FileStorage,
    request: Request,
) -> DocumentRow:
    """Приложение, которого система не нашла: новый документ работы, загруженный вручную."""
    data = await _read(request, file)
    if isinstance(data, int):
        return _too_large(publication_id, data)
    try:
        result = await documents.add_supplement(
            db, account.id, storage, _id(publication_id), file.filename or "upload", data
        )
    except documents.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Работа не найдена") from None
    return _answer(result, publication_id)


@router.post(
    "/documents/{document_id}/identity", operation_id="decideDocumentIdentity", responses=REFUSED
)
async def decide_document_identity(
    document_id: str, body: IdentityDecision, account: CurrentAccount, db: Db
) -> DocumentRow:
    """Решение пользователя: несовпадение не даёт использовать документ (AC05)."""
    try:
        result = await documents.decide_identity(
            db, account.id, _id(document_id), body.belongs_to_publication
        )
    except documents.NotFound:
        raise _unknown() from None
    return _answer(result, document_id)


def _not_started() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Исследование ещё не начато")


def _command(result: extraction.Operation | operations.Busy | extraction.Refused | None):
    if result is None:
        raise _not_started()
    if isinstance(result, operations.Busy):
        body = OperationInProgress(message=result.message).model_dump()
        return JSONResponse(body, status_code=status.HTTP_409_CONFLICT)
    if isinstance(result, extraction.Refused):
        body = Problem(target=result.target or "operation", message=result.message).model_dump()
        return JSONResponse(body, status_code=status.HTTP_409_CONFLICT)
    return _operation(result)


def _key(value: str) -> int:
    # Нечисловой ID не совпадёт ни с одной работой или документом: запуск будет отклонён.
    return int(value) if value.isdigit() else -1


@router.post(
    "/extraction/start",
    operation_id="startExtraction",
    status_code=status.HTTP_202_ACCEPTED,
    responses=REFUSED,
)
async def start_extraction(
    body: StartExtraction,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=200)],
    account: CurrentAccount,
    db: Db,
) -> Operation:
    """Запуск и дозапуск необработанных работ. Повтор с тем же ключом и входом возвращает ту же
    операцию; тот же ключ с другим входом — конфликт."""
    return _command(
        await extraction.start(
            db,
            account.id,
            idempotency_key,
            [_key(item) for item in body.publication_ids],
            [(_key(ref.document_id), ref.version) for ref in body.document_versions],
        )
    )


@router.post("/extraction/pause", operation_id="pauseExtraction")
async def pause_extraction(account: CurrentAccount, db: Db) -> Operation:
    """Пауза подтверждается на границе между работами; текущая работа завершается."""
    return _command(await extraction.pause(db, account.id))


@router.post("/extraction/resume", operation_id="resumeExtraction")
async def resume_extraction(account: CurrentAccount, db: Db) -> Operation:
    return _command(await extraction.resume(db, account.id))


@router.post("/extraction/stop", operation_id="stopExtraction")
async def stop_extraction(account: CurrentAccount, db: Db) -> Operation:
    """Остановка подтверждается на границе между работами; готовые итоги сохраняются."""
    return _command(await extraction.stop(db, account.id))


@router.post("/extraction/retry-failed", operation_id="retryFailedExtraction", responses=REFUSED)
async def retry_failed_extraction(account: CurrentAccount, db: Db) -> Operation:
    """Повтор только неудачных работ; готовые результаты и правки не затрагиваются."""
    return _command(await extraction.retry_failed(db, account.id))


@router.get("/extraction/log", operation_id="getExtractionLog")
async def get_extraction_log(account: CurrentAccount, db: Db) -> list[LogEvent]:
    events = await extraction.log(db, account.id)
    if events is None:
        raise _not_started()
    return [LogEvent(at=event.at.isoformat(), message=event.message) for event in events]


MEDIA_TYPES = {"pdf": "application/pdf", "jats": "application/xml", "csv": "text/csv"}


@router.get(
    "/documents/{document_id}/versions/{number}/file",
    operation_id="getDocumentVersionFile",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def get_document_version_file(
    document_id: str, number: int, account: CurrentAccount, db: Db, storage: FileStorage
) -> Response:
    """Файл конкретной версии документа. PDF открывается в браузере; остальное скачивается:
    файл из внешнего источника не исполняется на странице приложения."""
    try:
        found = await documents.version_file(db, account.id, storage, _id(document_id), number)
    except documents.NotFound:
        raise _unknown() from None
    disposition = "inline" if found.format == "pdf" else "attachment"
    return Response(
        found.data,
        media_type=MEDIA_TYPES.get(found.format, "application/octet-stream"),
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(found.name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )
