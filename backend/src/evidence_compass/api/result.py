"""Группа API «Результат»: охват, таблица, аналитика (ADR 0004, 0006).

Справка и основания строк — #17; проверка и исправление строк, обновление справки — #18,
экспорт CSV — #19: эти маршруты пока отвечают 501.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from evidence_compass import reports
from evidence_compass.api.auth import CurrentAccount, Db
from evidence_compass.cards import SetAsideKind
from evidence_compass.criteria import Problem, Resource, YearRange

router = APIRouter()

Freshness = Literal["current", "stale", "superseded"]
MeasureKind = Literal[
    "mean",
    "median",
    "observed_maximum",
    "individual_age",
    "modelled_age",
    "survival_interval",
    "maturity_age",
    "other",
]
# Пользовательская проверка извлечения; не оценка качества исследования.
ReviewStatus = Literal["unreviewed", "checked", "corrected", "rejected"]
# Серверная проверка привязки основания; техническая, не научная.
VerificationStatus = Literal["pending", "verified", "needs_review", "rejected"]
VerificationGap = Literal["quote_not_found", "value_not_matched"]
Setting = Literal["wild", "captive", "unknown"]
WorkOutcome = Literal["measures_extracted", "no_measures", "unresolved", "unavailable", "error"]


class Uncertainty(BaseModel):
    """Опубликованная неопределённость; отсутствующее не равно нулю."""

    label: str
    kind: str | None
    level_percent: float | None
    plus_minus: str | None
    lower: str | None
    upper: str | None
    # Почему граница не указана: not_reported, not_reached и т. п.
    upper_status: str | None


class Subject(BaseModel):
    label: str
    sex: str | None
    sample_size: str | None
    sample_size_scope: str | None


class Conditions(BaseModel):
    setting: Setting
    text: str


class MeasureRow(BaseModel):
    """Опубликованный показатель одной особи или группы (spec «Опубликованный показатель»)."""

    id: str
    # Номер строки в текущей ревизии, стабильный между ревизиями.
    number: int
    species_name_in_source: str
    # Подпись для группировки не стирает исходное название; оговорка сохраняет неопределённость.
    species_label: str
    taxonomy_note: str | None
    measure_kind: MeasureKind
    definition: str
    value_original: str
    unit_original: str
    qualifier: str | None
    uncertainty: Uncertainty | None
    subject: Subject
    conditions: Conditions
    method: str | None
    publication_id: str
    publication_label: str
    # Оговорки и ограничения, указанные авторами; пусто не означает их отсутствия.
    author_notes: str | None
    # Собственный результат работы или значение по другим работам, в т. ч. без ссылки во
    # введении (кандидат до проверки первоисточника).
    origin_kind: Literal["own_result", "secondary_report"]
    evidence_ids: list[str]
    context_evidence_ids: list[str]
    related_measure_ids: list[str]
    review_status: ReviewStatus
    verification_status: VerificationStatus
    # Почему автоматическая сверка не прошла; null — сверено.
    verification_gap: VerificationGap | None


class SearchQuery(BaseModel):
    resource: Resource
    query: str


class SearchParameters(BaseModel):
    period: YearRange
    publication_kinds: list[str]
    conditions: list[str]
    resources: list[Resource]
    queries: list[SearchQuery]


class FindingItem(BaseModel):
    measure_id: str
    number: int
    title: str
    context: str
    publication: str
    # Автоматическая сверка не прошла, строку сверил пользователь.
    checked_manually: bool = False


class Finding(BaseModel):
    text: str
    measure_refs: list[str]
    # Defaults preserve readability of previously saved report revisions.
    species: str | None = None
    items: list[FindingItem] = []


class SetAsideItem(BaseModel):
    """Что сознательно не стало показателем: реальный вид и описание из карточки, не общая
    фраза (симуляции, исходные данные и т. п.)."""

    kind: SetAsideKind
    description: str


class SummaryWork(BaseModel):
    """Работа, не давшая показателей в кратком выводе: итог обработки отдельно от причин."""

    publication_id: str
    label: str
    outcome: WorkOutcome | None
    # Неразрешённые расхождения карточки, требующие решения пользователя.
    unresolved: list[str] = []
    set_aside: list[SetAsideItem] = []
    # Пробелы документов работы (непрочитанные приложения и т. п.).
    gaps: list[str] = []


class Summary(BaseModel):
    """Краткий вывод: сначала находки со ссылками на строки, затем пределы вывода."""

    # Пусто — содержательного вывода нет (нет допустимых строк); причина — в `absent_reason`.
    findings: list[Finding]
    limits: list[str]
    absent_reason: str | None
    excluded_count: int = 0
    excluded_reasons: list[str] = []
    # По умолчанию — ранее сохранённые ревизии более старого шаблона читаются без поля.
    works: list[SummaryWork] = []


class CoverageWork(BaseModel):
    publication_id: str
    label: str
    outcome: WorkOutcome | None
    measure_count: int
    reason: str | None


class Coverage(BaseModel):
    found: int
    selected: int
    full_texts: int
    # Полный текст не получен.
    unavailable: int
    # Документ есть, но работа не обработана (например, файл требует проверки).
    not_processed: int
    # Доля выбранных работ, давших показатели, 0–1.
    share: float
    works: list[CoverageWork]


class Revision(BaseModel):
    """Ревизия таблицы; краткий вывод может быть построен по более ранней."""

    number: int
    # Ревизия таблицы, по которой построен краткий вывод.
    summary_revision: int
    # stale — краткий вывод отстаёт от таблицы; причина в `stale_reason`.
    freshness: Freshness
    stale_reason: str | None
    # Результат неполный: не все выбранные работы обработаны.
    complete: bool


class Result(BaseModel):
    revision: Revision
    question: str
    search: SearchParameters
    summary: Summary
    coverage: Coverage
    measures: list[MeasureRow]
    allowed_actions: list[Literal["refresh", "export"]]


LocatorKind = Literal["pdf", "jats", "jats_row", "jats_cell", "csv"]


class Locator(BaseModel):
    """Адрес основания в версии документа (spec «Адресуемое основание»).

    PDF — физическая страница; JATS — путь и ID узла, для таблицы — строка и столбец с
    заголовками; CSV — номер записи (не физической строки) и столбец.
    """

    kind: LocatorKind
    page: int | None
    # Адрес словами: «страница 4», «Table 2 · строка 3 · столбец 2 «Age»», «запись 5».
    printed: str
    context: str | None
    path: str | None
    node_id: str | None
    # Ярлык таблицы JATS, например «Table 2.».
    table: str | None
    row: int | None
    column: int | None
    headers: list[str]
    record: int | None


class FragmentLine(BaseModel):
    number: int
    text: str
    highlighted: bool


class SourceMeta(BaseModel):
    resource: str
    identifier: str
    source_type: str
    year: int | None


class Evidence(BaseModel):
    id: str
    measure_id: str
    # Основание значения или контекст (методы, условия).
    role: Literal["value", "context"]
    publication_label: str
    document_id: str
    document_version_id: str
    document_name: str
    document_version: int
    # Цитата и место так, как их указала модель; не фрагмент документа.
    quote: str
    claimed_location: str
    # None — место цитаты в документе не установлено; адрес не выдумывается.
    locator: Locator | None
    # None — точный фрагмент показать нельзя; `precision_note` объясняет, куда перейти.
    fragment: list[FragmentLine] | None
    precision_note: str | None
    # Почему привязка требует проверки или чем место отличается от указанного моделью.
    diagnostic: str | None
    source: SourceMeta
    verification_status: VerificationStatus


class MeasureCorrection(BaseModel):
    """Исправление значения; `expected_revision` предотвращает потерю параллельной правки."""

    expected_revision: int
    value_original: str
    unit_original: str
    comment: str


class MeasureReview(BaseModel):
    review_status: Literal["checked", "rejected"]


def _not_implemented() -> HTTPException:
    return HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Контракт описан, реализация — #18–#19")


@router.get("/result", operation_id="getResult")
async def get_result(account: CurrentAccount, db: Db) -> Result:
    """Текущая ревизия справки: закреплённые версии строк и карточек, охват и краткий вывод."""
    report = await reports.current(db, account.id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Результата ещё нет: запустите извлечение")
    return Result.model_validate(report, from_attributes=True)


@router.get("/measures/{measure_id}/evidence", operation_id="getMeasureEvidence")
async def get_measure_evidence(measure_id: str, account: CurrentAccount, db: Db) -> list[Evidence]:
    """Все основания строки: значения и контекста; потеря дополнительных оснований недопустима."""
    try:
        if not measure_id.isdigit():
            raise reports.NotFound
        found = await reports.measure_evidence(db, account.id, int(measure_id))
    except reports.NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Строка не найдена") from None
    return [Evidence.model_validate(item) for item in found]


@router.post("/measures/{measure_id}/review", operation_id="reviewMeasure")
async def review_measure(measure_id: str, body: MeasureReview) -> MeasureRow:
    raise _not_implemented()


@router.post(
    "/measures/{measure_id}/correction",
    operation_id="correctMeasure",
    responses={409: {"model": Problem, "description": "Строка изменена параллельно"}},
)
async def correct_measure(measure_id: str, body: MeasureCorrection) -> MeasureRow:
    """Правка сохраняет историю и делает зависимый краткий вывод устаревшим."""
    raise _not_implemented()


@router.post("/result/refresh", operation_id="refreshResult")
async def refresh_result() -> Result:
    """Явное обновление справки с учётом сохранённых правок."""
    raise _not_implemented()


@router.get(
    "/result/export",
    operation_id="exportResult",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/csv": {}}},
        409: {"model": Problem, "description": "Ревизия больше не текущая"},
    },
)
async def export_result(revision: int) -> str:
    """CSV именно указанной ревизии таблицы, не библиографического списка."""
    raise _not_implemented()
