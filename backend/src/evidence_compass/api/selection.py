"""Группы API «Поиск» и «Отбор» (ADR 0006).

Поиск — #11, отбор — #12. Пока общий слот занят, изменения отбора отвечают 409.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from evidence_compass import documents, operations, search, selection, sources
from evidence_compass.api.auth import CurrentAccount, Db
from evidence_compass.api.study import OperationInProgress, ServiceProblem, Step
from evidence_compass.criteria import Problem, Resource

# Исход каждого ресурса независим: успешная пустая выдача и ошибка — разные состояния.
from evidence_compass.search import SearchAction, SearchStatus, SourceStatus

router = APIRouter()

Decision = Literal["included", "excluded", "needs_review"]
VersionType = Literal["journal", "preprint"]
Provenance = Literal["pubmed", "europe_pmc", "manual"]
# Открытый текст по сведениям Europe PMC; получение документа — отдельный шаг (#13).
OpenAccess = Literal["open", "closed", "unknown"]
VersionLink = Literal["none", "confirmed", "needs_review"]


class SourceRun(BaseModel):
    resource: Resource
    # Фактический текст запроса к ресурсу, построенный из подтверждённых критериев.
    query: str
    status: SourceStatus
    hit_count: int | None
    # Число фактически сохранённых записей, не общее число совпадений источника.
    returned_count: int | None
    error: str | None


class SearchRun(BaseModel):
    status: SearchStatus
    criteria_version: int
    # Доля завершённых ресурсов, 0–1.
    progress: float
    sources: list[SourceRun]
    allowed_actions: list[SearchAction]
    # Причина, по которой переход к отбору пока недоступен; None — доступен.
    selection_blocked_reason: str | None


class PublicationRow(BaseModel):
    id: str
    title: str
    authors: str
    doi: str | None
    year: int | None
    version_type: VersionType
    provenance: list[Provenance]
    decision: Decision
    versions: int
    version_link: VersionLink
    open_access: OpenAccess
    selected: bool
    # Причина, по которой выбрать нельзя; None — можно.
    select_blocked_reason: str | None


class Selection(BaseModel):
    publications: list[PublicationRow]
    selected_count: int
    limit: int
    # Причина, по которой перейти к документам нельзя; None — можно.
    proceed_blocked_reason: str | None


class DecisionRecord(BaseModel):
    decision: Decision
    reason: str
    author: Literal["system", "user"]
    criteria_version: int


class SourceRecord(BaseModel):
    resource: Provenance
    identifier: str
    url: str | None


class RelatedVersion(BaseModel):
    id: str
    title: str
    version_type: VersionType
    link: VersionLink


class PublicationCard(PublicationRow):
    abstract: str | None
    sources: list[SourceRecord]
    decision_record: DecisionRecord
    related_versions: list[RelatedVersion]


class DecisionChange(BaseModel):
    decision: Decision
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SelectionChange(BaseModel):
    selected: bool


class VersionLinkChange(BaseModel):
    related_id: str
    same_work: bool


class LookupQuery(BaseModel):
    """DOI, PMID или название."""

    query: str


class LookupCandidate(BaseModel):
    title: str
    year: int | None
    doi: str | None
    pmid: str | None
    already_listed: bool


class ManualAddition(BaseModel):
    doi: str | None
    pmid: str | None


class ProceedResult(BaseModel):
    reached_step: Step


class SearchRefused(BaseModel):
    """Поиск не запущен: слот занят другой операцией или критерии нельзя выразить в запросе."""

    message: str


def _not_confirmed() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Критерии ещё не подтверждались")


def _run(run: search.Run) -> SearchRun:
    return SearchRun(
        status=run.status,
        criteria_version=run.criteria_version,
        progress=run.progress,
        sources=[
            SourceRun(
                resource=item.resource,
                query=item.query,
                status=item.status,
                hit_count=item.hit_count,
                returned_count=item.returned_count,
                error=item.error,
            )
            for item in run.sources
        ],
        allowed_actions=run.allowed_actions,
        selection_blocked_reason=run.selection_blocked_reason,
    )


@router.get("/search", operation_id="getSearch")
async def get_search(account: CurrentAccount, db: Db) -> SearchRun:
    """Последний запуск по последней подтверждённой версии критериев или его запросы до запуска."""
    run = await search.get(db, account.id)
    if run is None:
        raise _not_confirmed()
    return _run(run)


@router.post(
    "/search/start",
    operation_id="startSearch",
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"model": SearchRefused}},
)
async def start_search(account: CurrentAccount, db: Db) -> SearchRun:
    """Запуск и повтор поиска по последней подтверждённой версии критериев.

    Каждый запуск — новый; прежний остаётся связанным со своей версией критериев.
    """
    result = await search.start(db, account.id)
    match result:
        case None:
            raise _not_confirmed()
        case operations.Busy(message=message) | search.Refused(message=message):
            return JSONResponse(
                SearchRefused(message=message).model_dump(), status_code=status.HTTP_409_CONFLICT
            )
    return _run(result)


@router.post("/search/stop", operation_id="stopSearch")
async def stop_search(account: CurrentAccount, db: Db) -> SearchRun:
    """Останавливает незавершённый запуск; полученные исходы источников сохраняются."""
    run = await search.stop(db, account.id)
    if run is None:
        raise _not_confirmed()
    return _run(run)


def _publication_row(row: selection.Row) -> PublicationRow:
    return PublicationRow(
        id=str(row.id),
        title=row.title,
        authors=row.authors,
        doi=row.doi,
        year=row.year,
        version_type=row.version_type,
        provenance=row.provenance,
        decision=row.decision,
        versions=row.versions,
        version_link=row.version_link,
        open_access=row.open_access,
        selected=row.selected,
        select_blocked_reason=row.select_blocked_reason,
    )


def _selection(found: selection.Selection) -> Selection:
    return Selection(
        publications=[_publication_row(row) for row in found.publications],
        selected_count=found.selected_count,
        limit=selection.LIMIT,
        proceed_blocked_reason=found.proceed_blocked_reason,
    )


def _card(card: selection.Card) -> PublicationCard:
    return PublicationCard(
        **_publication_row(card.row).model_dump(),
        abstract=card.abstract,
        sources=[
            SourceRecord(resource=s.resource, identifier=s.identifier, url=s.url)
            for s in card.sources
        ],
        decision_record=DecisionRecord(
            decision=card.decision_record.decision,
            reason=card.decision_record.reason,
            author=card.decision_record.author,
            criteria_version=card.decision_record.criteria_version,
        ),
        related_versions=[
            RelatedVersion(id=str(r.id), title=r.title, version_type=r.version_type, link=r.link)
            for r in card.related_versions
        ],
    )


def _refused(result: operations.Busy | selection.Refused) -> JSONResponse:
    if isinstance(result, operations.Busy):
        body = OperationInProgress(message=result.message).model_dump()
    else:
        body = Problem(target=result.target, message=result.message).model_dump()
    return JSONResponse(body, status_code=status.HTTP_409_CONFLICT)


def _unknown() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Публикация не найдена")


def _id(publication_id: str) -> int:
    if not publication_id.isdigit():
        raise _unknown()
    return int(publication_id)


def get_source(request: Request) -> sources.Source:
    return request.app.state.lookup_source


LookupSource = Annotated[sources.Source, Depends(get_source)]
REFUSED = {409: {"model": Problem | OperationInProgress}}
LOOKUP_FAILED = {503: {"model": ServiceProblem}}


def _lookup_failed(failure: sources.SourceFailed) -> JSONResponse:
    body = ServiceProblem(message=f"Поиск записи не выполнен: {failure}").model_dump()
    return JSONResponse(body, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


@router.get("/selection", operation_id="getSelection")
async def get_selection(account: CurrentAccount, db: Db) -> Selection:
    """Записи последнего завершённого поиска и добавленные вручную, без скрытия за лимитом."""
    found = await selection.get(db, account.id)
    if found is None:
        raise _not_confirmed()
    return _selection(found)


@router.get("/publications/{publication_id}", operation_id="getPublication")
async def get_publication(publication_id: str, account: CurrentAccount, db: Db) -> PublicationCard:
    try:
        return _card(await selection.card(db, account.id, _id(publication_id)))
    except selection.NotFound:
        raise _unknown() from None


@router.put(
    "/publications/{publication_id}/decision", operation_id="changeDecision", responses=REFUSED
)
async def change_decision(
    publication_id: str, body: DecisionChange, account: CurrentAccount, db: Db
) -> PublicationCard:
    """Решение пользователя сохраняется с причиной и версией критериев; прежние — история."""
    try:
        result = await selection.change_decision(
            db, account.id, _id(publication_id), body.decision, body.reason
        )
    except selection.NotFound:
        raise _unknown() from None
    if isinstance(result, selection.Card):
        return _card(result)
    return _refused(result)


@router.put(
    "/publications/{publication_id}/selected",
    operation_id="changeSelected",
    responses={
        409: {
            "model": Problem | OperationInProgress,
            "description": "Лимит выбора, недопустимая работа или занятый слот",
        }
    },
)
async def change_selected(
    publication_id: str, body: SelectionChange, account: CurrentAccount, db: Db
) -> Selection:
    """Выбор записи, ждущей решения, включает её от имени пользователя; максимум восемь."""
    try:
        result = await selection.change_selected(db, account.id, _id(publication_id), body.selected)
    except selection.NotFound:
        raise _unknown() from None
    if isinstance(result, selection.Selection):
        # Работа, выбранная после перехода к документам, сразу получает основной текст.
        await documents.ensure(db, account.id)
        return _selection(result)
    return _refused(result)


@router.post(
    "/publications/{publication_id}/version-links",
    operation_id="resolveVersionLink",
    responses=REFUSED,
)
async def resolve_version_link(
    publication_id: str, body: VersionLinkChange, account: CurrentAccount, db: Db
) -> PublicationCard:
    """Связь версий устанавливается или отклоняется только решением пользователя."""
    try:
        result = await selection.resolve_version_link(
            db, account.id, _id(publication_id), _id(body.related_id), body.same_work
        )
    except selection.NotFound:
        raise _unknown() from None
    if isinstance(result, selection.Card):
        return _card(result)
    return _refused(result)


@router.post("/publications/lookup", operation_id="lookupPublication", responses=LOOKUP_FAILED)
async def lookup_publication(
    body: LookupQuery, account: CurrentAccount, db: Db, source: LookupSource
) -> list[LookupCandidate]:
    try:
        found = await selection.lookup(db, account.id, source, body.query)
    except sources.SourceFailed as failure:
        return _lookup_failed(failure)
    if found is None:
        raise _not_confirmed()
    return [
        LookupCandidate(
            title=item.record.title,
            year=item.record.year,
            doi=item.record.doi,
            pmid=item.record.pmid,
            already_listed=item.already_listed,
        )
        for item in found
    ]


@router.post(
    "/publications/manual",
    operation_id="addPublicationManually",
    responses={**REFUSED, **LOOKUP_FAILED},
)
async def add_publication_manually(
    body: ManualAddition, account: CurrentAccount, db: Db, source: LookupSource
) -> PublicationRow:
    """Запись с постоянной меткой «Добавлено вручную»; ждёт решения о соответствии."""
    if await selection.get(db, account.id) is None:
        raise _not_confirmed()
    try:
        result = await selection.add_manually(db, account.id, source, body.doi, body.pmid)
    except sources.SourceFailed as failure:
        return _lookup_failed(failure)
    match result:
        case None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Запись не найдена")
        case selection.Row():
            return _publication_row(result)
    return _refused(result)


@router.post(
    "/selection/proceed",
    operation_id="proceedToDocuments",
    responses={409: {"model": Problem}},
)
async def proceed_to_documents(account: CurrentAccount, db: Db) -> ProceedResult:
    if await selection.get(db, account.id) is None:
        raise _not_confirmed()
    refused = await selection.proceed(db, account.id)
    if refused:
        return _refused(refused)
    await documents.ensure(db, account.id)
    return ProceedResult(reached_step="preparation")
