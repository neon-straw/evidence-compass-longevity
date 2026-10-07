"""Группа API «Вопрос и критерии» (ADR 0006).

Вопрос и его версии — #9; предложения модели, черновик и версии критериев — #10.
Пока общий слот занят операцией, изменяющие команды отвечают 409, чтение доступно.
"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from evidence_compass import criteria, operations, questions
from evidence_compass.api.auth import CurrentAccount, Db
from evidence_compass.criteria import (
    CriteriaAction,
    CriteriaFields,
    DraftStatus,
    Problem,
    SearchCriterion,
)
from evidence_compass.llm import LLM

router = APIRouter()

Step = Literal["question", "criteria", "search", "selection", "preparation", "result"]


class Study(BaseModel):
    """Текущее исследование: один вопрос и достигнутый шаг маршрута."""

    reached_step: Step


class QuestionDraft(BaseModel):
    text: str
    saved_at: str | None = Field(description="ISO 8601; None — черновик ещё не сохранялся")


class QuestionText(BaseModel):
    text: Annotated[str, Field(max_length=5000)]


class QuestionVersion(BaseModel):
    """Неизменяемый снимок формулировки на один запуск подготовки критериев."""

    version: int
    text: str
    created_at: str


class UnsupportedQuestion(BaseModel):
    """Отказ готовить критерии.

    Вопрос вне формы Stage 1: `example` — как переформулировать, новую форму результата
    система не придумывает. Пустой черновик: только `message`.
    """

    message: str
    example: str | None = None


class CriteriaDraft(CriteriaFields):
    status: DraftStatus
    # Конкретные пробелы при частично полученных предложениях системы.
    gaps: list[str]
    problems: list[Problem]
    # Номер последней подтверждённой версии; None — ещё не подтверждались.
    confirmed_version: int | None
    # Черновик отличается от последней подтверждённой версии.
    has_unconfirmed_changes: bool
    # Действия, доступные сейчас; причины недоступности — в `problems`.
    allowed_actions: list[CriteriaAction]
    saved_at: str | None


class ServiceProblem(BaseModel):
    """Внешний сервис не ответил; вопрос и сохранённые данные не изменились."""

    message: str


class OperationInProgress(BaseModel):
    """Общий слот занят незавершённой операцией; сохранённое можно читать."""

    message: str


class CriteriaProposal(BaseModel):
    """Новая редакция по вопросу; применяется только после подтверждения пользователя."""

    search_criteria: list[SearchCriterion]


class CriteriaVersion(BaseModel):
    version: int


def get_llm(request: Request) -> LLM | None:
    return request.app.state.llm


ModelConnection = Annotated[LLM | None, Depends(get_llm)]
BUSY = {409: {"model": OperationInProgress}}


def _not_started() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Критерии ещё не готовились")


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _draft(draft: questions.Draft) -> QuestionDraft:
    return QuestionDraft(text=draft.text, saved_at=_iso(draft.saved_at))


def _criteria_draft(draft: criteria.Draft) -> CriteriaDraft:
    return CriteriaDraft(
        **draft.fields.model_dump(),
        status=draft.status,
        gaps=draft.gaps,
        problems=draft.problems,
        confirmed_version=draft.confirmed_version,
        has_unconfirmed_changes=draft.has_unconfirmed_changes,
        allowed_actions=draft.allowed_actions,
        saved_at=_iso(draft.saved_at),
    )


def _refusal(model: BaseModel, code: int) -> JSONResponse:
    # Необязательный `example` отказа по вопросу опускается, а не приходит как null.
    body = model.model_dump(exclude_none=isinstance(model, UnsupportedQuestion))
    return JSONResponse(body, status_code=code)


def _busy(busy: operations.Busy) -> JSONResponse:
    return _refusal(OperationInProgress(message=busy.message), status.HTTP_409_CONFLICT)


def _opened(
    result: criteria.Draft | questions.NotReady | operations.Busy | criteria.Unavailable,
) -> CriteriaDraft | JSONResponse:
    match result:
        case questions.NotReady(message=message):
            return _refusal(
                UnsupportedQuestion(message=message), status.HTTP_422_UNPROCESSABLE_CONTENT
            )
        case operations.Busy():
            return _busy(result)
        case criteria.Unavailable(message=message):
            return _refusal(ServiceProblem(message=message), status.HTTP_503_SERVICE_UNAVAILABLE)
    return _criteria_draft(result)


@router.get("/study", operation_id="getStudy")
async def get_study(account: CurrentAccount, db: Db) -> Study:
    return Study(reached_step=await questions.reached_step(db, account.id))


@router.get("/question", operation_id="getQuestion")
async def get_question(account: CurrentAccount, db: Db) -> QuestionDraft:
    return _draft(await questions.get_draft(db, account.id))


@router.put("/question", operation_id="saveQuestion", responses=BUSY)
async def save_question(body: QuestionText, account: CurrentAccount, db: Db) -> QuestionDraft:
    if busy := await operations.busy(db):
        return _busy(busy)
    return _draft(await questions.save_draft(db, account.id, body.text))


@router.get("/question/versions", operation_id="listQuestionVersions")
async def list_question_versions(account: CurrentAccount, db: Db) -> list[QuestionVersion]:
    return [
        QuestionVersion(
            version=item.version, text=item.text, created_at=item.created_at.isoformat()
        )
        for item in await questions.list_versions(db, account.id)
    ]


@router.post(
    "/question/prepare-criteria",
    operation_id="prepareCriteria",
    status_code=status.HTTP_202_ACCEPTED,
    responses={422: {"model": UnsupportedQuestion}, 503: {"model": ServiceProblem}, **BUSY},
)
async def prepare_criteria(account: CurrentAccount, db: Db, llm: ModelConnection) -> CriteriaDraft:
    """Ставит подготовку предложений в общий слот; готовность видна в `GET /criteria`."""
    return _opened(await criteria.prepare(db, account.id, llm))


@router.post(
    "/criteria/start-manually",
    operation_id="startCriteriaManually",
    responses={422: {"model": UnsupportedQuestion}, **BUSY},
)
async def start_criteria_manually(
    account: CurrentAccount, db: Db, llm: ModelConnection
) -> CriteriaDraft:
    """Переход к ручному заполнению, если подготовка предложений не удалась."""
    return _opened(await criteria.start_manually(db, account.id, llm))


@router.get("/criteria", operation_id="getCriteria")
async def get_criteria(account: CurrentAccount, db: Db, llm: ModelConnection) -> CriteriaDraft:
    draft = await criteria.get(db, account.id, llm)
    if draft is None:
        raise _not_started()
    return _criteria_draft(draft)


@router.put("/criteria", operation_id="saveCriteria", responses=BUSY)
async def save_criteria(
    body: CriteriaFields, account: CurrentAccount, db: Db, llm: ModelConnection
) -> CriteriaDraft:
    result = await criteria.save(db, account.id, body, llm)
    if result is None:
        raise _not_started()
    if isinstance(result, operations.Busy):
        return _busy(result)
    return _criteria_draft(result)


@router.post(
    "/criteria/reformulate",
    operation_id="reformulateCriteria",
    responses={503: {"model": ServiceProblem}, **BUSY},
)
async def reformulate_criteria(
    account: CurrentAccount, db: Db, llm: ModelConnection
) -> CriteriaProposal:
    """Новая редакция по вопросу; черновик меняется, только если пользователь её применит."""
    result = await criteria.reformulate(db, account.id, llm)
    match result:
        case None:
            raise _not_started()
        case operations.Busy():
            return _busy(result)
        case criteria.Unavailable(message=message):
            return _refusal(ServiceProblem(message=message), status.HTTP_503_SERVICE_UNAVAILABLE)
    return CriteriaProposal(search_criteria=result)


@router.post(
    "/criteria/confirm",
    operation_id="confirmCriteria",
    responses={409: {"model": CriteriaDraft, "description": "Подтверждение заблокировано"}},
)
async def confirm_criteria(
    account: CurrentAccount, db: Db, llm: ModelConnection
) -> CriteriaVersion:
    result = await criteria.confirm(db, account.id, llm)
    if result is None:
        raise _not_started()
    if isinstance(result, criteria.Blocked):
        return _refusal(_criteria_draft(result.draft), status.HTTP_409_CONFLICT)
    return CriteriaVersion(version=result)
