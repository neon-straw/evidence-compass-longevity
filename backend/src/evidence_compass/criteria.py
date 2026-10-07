"""Критерии поиска: предложения модели, черновик и неизменяемые версии (spec, п. 4; AC01).

Все подтверждённые критерии применяются только в запросах PubMed и Europe PMC. Критерий,
который нельзя представить в запросе источника, блокирует подтверждение с объяснением.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass import operations, questions
from evidence_compass.db import Base
from evidence_compass.llm import LLM, Completion, LLMUnavailable
from evidence_compass.operations import Busy
from evidence_compass.search_queries import MAX_QUERY_LEN, query

log = logging.getLogger(__name__)

# Происхождение строки: предложено системой, изменено или добавлено пользователем.
Origin = Literal["suggested", "edited", "added"]
ArticleType = Literal["primary_study", "review", "meta_analysis", "other"]
PublicationKind = Literal["journal_article", "preprint", "other"]
Condition = Literal["wild", "captive"]
Resource = Literal["pubmed", "europe_pmc"]
CriteriaAction = Literal["confirm", "reformulate"]
DraftStatus = Literal["preparing", "ready", "partial", "failed"]

NO_MODEL = (
    "Подключение к модели не настроено: предложения критериев недоступны. "
    "Заполните критерии вручную."
)


class StudyObject(BaseModel):
    id: str
    name: str
    scientific_name: str
    origin: Origin
    aliases: list[str] = Field(default_factory=list)
    suggested_aliases: list[str] = Field(default_factory=list)


class YearRange(BaseModel):
    from_year: int | None
    to_year: int | None


class SearchCriterion(BaseModel):
    id: str
    label: str
    terms: list[str]
    # Термины, предложенные системой: по ним видно, какие термины пользователь добавил или убрал.
    suggested_terms: list[str]
    origin: Origin


class CriteriaFields(BaseModel):
    """Редактируемая часть критериев; все поля попадают в запросы PubMed и Europe PMC."""

    objects: list[StudyObject]
    period: YearRange
    article_types: list[ArticleType]
    publication_kinds: list[PublicationKind]
    conditions: list[Condition]
    search_criteria: list[SearchCriterion]
    resources: list[Resource]


class Problem(BaseModel):
    """Причина, по которой подтверждение недоступно; `target` — id строки или имя поля."""

    target: str
    message: str


# По умолчанию период и условия не ограничены, препринты включены (spec, п. 4).
DEFAULT_FIELDS = CriteriaFields(
    objects=[],
    period=YearRange(from_year=None, to_year=None),
    article_types=["primary_study", "review", "meta_analysis", "other"],
    publication_kinds=["journal_article", "preprint"],
    conditions=["wild", "captive"],
    search_criteria=[],
    resources=["pubmed", "europe_pmc"],
)


class CriteriaDraftRecord(Base):
    """Редактируемый черновик критериев исследования; версии — отдельные строки."""

    __tablename__ = "criteria_drafts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('preparing', 'ready', 'partial', 'failed')", name="status_known"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), unique=True)
    # Версия вопроса, по которой готовились критерии.
    question_version_id: Mapped[int] = mapped_column(ForeignKey("question_versions.id"))
    status: Mapped[str] = mapped_column(String(16))
    fields: Mapped[dict] = mapped_column(JSONB)
    # Конкретные пробелы предложений модели, показываются пользователю как есть.
    gaps: Mapped[list[str]] = mapped_column(JSONB, default=list)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CriteriaVersionRecord(Base):
    """Неизменяемая подтверждённая версия критериев. Строка не обновляется."""

    __tablename__ = "criteria_versions"
    __table_args__ = (UniqueConstraint("study_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    question_version_id: Mapped[int] = mapped_column(ForeignKey("question_versions.id"))
    number: Mapped[int]
    fields: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Ответ модели (structured output, ADR 0007). Идентификаторы и происхождение задаёт сервер.
class ProposedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Название вида по-русски, как в вопросе")
    scientific_name: str | None = Field(
        description="Латинское биноминальное название; null, если нет уверенности"
    )
    aliases: list[str] = Field(
        description="Английские названия вида и варианты написания для расширения поиска; "
        "без операторов и кавычек; пустой список, если нет уверенности"
    )


class ProposedCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Короткое название критерия по-русски")
    terms: list[str] = Field(
        description="Английские поисковые термины для PubMed и Europe PMC, без кавычек и операторов"
    )


class CriteriaSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects: list[ProposedObject]
    search_criteria: list[ProposedCriterion]


SYSTEM_PROMPT = """\
Ты помогаешь научному сотруднику подготовить поиск публикаций в PubMed и Europe PMC.
Вопрос касается опубликованных показателей возраста или продолжительности жизни животных.
Выдели из вопроса виды животных с латинскими названиями. Не придумывай научное название, \
если не уверен: верни null. В aliases предложи английские названия вида и варианты \
написания. Это расширение текстового поиска, а не утверждение тождества таксонов. \
Названия видов возвращай только в objects, не дублируй их в search_criteria.
Каждый search_criteria — группа английских терминов-синонимов одной темы. Внутри группы \
термины соединяются OR, все группы — AND: каждая дополнительная группа исключает статьи.
Предложи тематическую группу показателей возраста/продолжительности жизни. Дополнительные \
группы добавляй только для явно запрошенных ограничений на включение публикаций. \
Не превращай сведения, которые пользователь хочет извлечь из статьи, в условия поиска. \
Запрос численных показателей не требует группы numerical/quantitative. Методы определения \
возраста и условия содержания не являются обязательными, если вопрос их не ограничивает.
Пример: «Какие численные показатели продолжительности жизни опубликованы для голого \
землекопа и гренландской акулы?» — два объекта с aliases и одна тематическая группа \
показателей; без групп видов, методов, среды и численного формата.
Пример: «Какие показатели возраста гренландской акулы получены только в исследованиях \
с радиоуглеродным датированием?» — объект, группа возраста и отдельная группа \
радиоуглеродного датирования. Но «укажи, каким методом оценивали возраст» — пожелание \
к извлекаемым сведениям, а не обязательный метод.
Не добавляй ограничения, которых нет в вопросе, и не меняй форму результата."""


@dataclass(frozen=True)
class Draft:
    fields: CriteriaFields
    status: DraftStatus
    gaps: list[str]
    problems: list[Problem]
    confirmed_version: int | None
    has_unconfirmed_changes: bool
    allowed_actions: list[CriteriaAction]
    saved_at: datetime | None


@dataclass(frozen=True)
class Unavailable:
    """Модель недоступна или не настроена; сохранённые данные не изменились."""

    message: str


@dataclass(frozen=True)
class Blocked:
    draft: Draft


# Термин передаётся источнику как текст; синтаксис запроса PubMed и Europe PMC
# (фразы, группы, теги полей, усечение, логические операторы) изменил бы его смысл.
QUERY_SYNTAX = re.compile(r'["()\[\]*?:\\]|\b(?:AND|OR|NOT)\b')


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def problems(fields: CriteriaFields) -> list[Problem]:
    """Причины, по которым критерии нельзя представить в запросах источников."""
    found: list[Problem] = []
    if not fields.objects and not fields.search_criteria:
        found.append(
            Problem(
                target="search_criteria",
                message="Запрос пуст: добавьте хотя бы один вид или критерий поискового запроса.",
            )
        )
    for item in fields.objects:
        if any(QUERY_SYNTAX.search(alias) for alias in item.aliases):
            found.append(
                Problem(
                    target=item.id,
                    message="Поисковые названия содержат синтаксис запроса; "
                    "укажите только названия и варианты написания без операторов и кавычек.",
                )
            )
        if QUERY_SYNTAX.search(item.scientific_name):
            found.append(
                Problem(
                    target=item.id,
                    message="Научное название содержит синтаксис запроса; "
                    "укажите только название вида.",
                )
            )
        if not item.scientific_name.strip():
            found.append(
                Problem(
                    target=item.id,
                    message=f"Для «{item.name or 'без названия'}» не указано научное название: "
                    "вид нельзя представить в запросе.",
                )
            )
    period = fields.period
    if period.from_year and period.to_year and period.from_year > period.to_year:
        found.append(
            Problem(
                target="period",
                message="Начало периода позже его конца: такой период нельзя представить "
                "в запросе.",
            )
        )
    for criterion in fields.search_criteria:
        name = criterion.label or "без названия"
        terms = [term for term in criterion.terms if term.strip()]
        syntax = next((term for term in terms if QUERY_SYNTAX.search(term)), None)
        if not terms:
            message = f"Критерий «{name}» нельзя представить в запросе: добавьте поисковые термины."
        elif syntax:
            message = (
                f"Критерий «{name}»: термин «{syntax}» содержит синтаксис запроса (кавычки, "
                "скобки, двоеточие, обратный слеш, *, ?, AND/OR/NOT) — "
                "источник поймёт его иначе; уберите эти знаки."
            )
        else:
            continue
        found.append(Problem(target=criterion.id, message=message))
    # Пустой выбор из словаря не представим в запросе: он не нашёл бы ни одной записи.
    for target, selected, what in (
        ("publication_kinds", fields.publication_kinds, "вид публикации"),
        ("article_types", fields.article_types, "тип статьи"),
        ("conditions", fields.conditions, "условие наблюдения"),
    ):
        if not selected:
            found.append(Problem(target=target, message=f"Выберите хотя бы один {what}."))
    if not fields.resources:
        found.append(Problem(target="resources", message="Выберите хотя бы один ресурс поиска."))
    if not found:
        for resource in fields.resources:
            rendered = query(resource, fields)
            if len(rendered) > MAX_QUERY_LEN:
                name = "PubMed" if resource == "pubmed" else "Europe PMC"
                found.append(
                    Problem(
                        target="search_criteria",
                        message=f"Запрос {name} превышает {MAX_QUERY_LEN} символов. "
                        "Сократите термины или число критериев.",
                    )
                )
    return found


async def _records(
    db: AsyncSession, study_id: int, *, lock: bool = False
) -> tuple[CriteriaDraftRecord | None, CriteriaVersionRecord | None]:
    statement = select(CriteriaDraftRecord).where(CriteriaDraftRecord.study_id == study_id)
    draft = await db.scalar(statement.with_for_update() if lock else statement)
    latest = await db.scalar(
        select(CriteriaVersionRecord)
        .where(CriteriaVersionRecord.study_id == study_id)
        .order_by(CriteriaVersionRecord.number.desc())
        .limit(1)
    )
    return draft, latest


async def _view(
    db: AsyncSession,
    record: CriteriaDraftRecord,
    latest: CriteriaVersionRecord | None,
    llm: LLM | None,
) -> Draft:
    fields = CriteriaFields.model_validate(record.fields)
    status = cast(DraftStatus, record.status)
    operation = await operations.active(db)
    if status == "preparing":
        found: list[Problem] = []
        allowed: list[CriteriaAction] = []
        shown = fields.model_copy(update={"objects": [], "search_criteria": []})
    else:
        found = problems(fields)
        if operation is not None:
            found.insert(0, Problem(target="operation", message=operations.busy_message(operation)))
        allowed = []
        if operation is None:
            allowed = ([] if found else ["confirm"]) + (["reformulate"] if llm else [])
        shown = fields
    return Draft(
        fields=shown,
        status=status,
        gaps=list(record.gaps),
        problems=found,
        confirmed_version=latest.number if latest else None,
        has_unconfirmed_changes=latest is None or latest.fields != record.fields,
        allowed_actions=allowed,
        saved_at=record.saved_at,
    )


async def get(db: AsyncSession, account_id: int, llm: LLM | None) -> Draft | None:
    study = await questions.find_study(db, account_id)
    if study is None:
        return None
    record, latest = await _records(db, study.id)
    return await _view(db, record, latest, llm) if record else None


async def _open(
    db: AsyncSession, account_id: int
) -> tuple[CriteriaDraftRecord, questions.QuestionVersionRecord] | questions.NotReady | Busy:
    """Фиксирует версию вопроса и открывает черновик критериев в общей транзакции."""
    frozen = await questions.freeze(db, account_id)
    if isinstance(frozen, questions.NotReady):
        await db.rollback()
        return frozen
    study, version = frozen
    if busy := await operations.busy(db):
        return busy
    record, _ = await _records(db, study.id, lock=True)
    if record is None:
        record = CriteriaDraftRecord(
            study_id=study.id, question_version_id=version.id, fields=DEFAULT_FIELDS.model_dump()
        )
        db.add(record)
    record.gaps = []
    return record, version


async def prepare(
    db: AsyncSession, account_id: int, llm: LLM | None
) -> Draft | questions.NotReady | Busy | Unavailable:
    """Ставит подготовку предложений в общий слот; отвечает worker (ADR 0008).

    Прежние виды и критерии остаются в черновике до ответа модели: отказ их не стирает,
    и они остаются привязаны к вопросу, по которому готовились.
    """
    if llm is None:
        return Unavailable(NO_MODEL)
    opened = await _open(db, account_id)
    if not isinstance(opened, tuple):
        return opened
    record, version = opened
    record.status = "preparing"
    db.add(
        operations.OperationRecord(
            study_id=record.study_id,
            kind="criteria_preparation",
            status="queued",
            input={"question_version_id": version.id},
            model=llm.model,
            created_at=datetime.now(UTC),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Слот занят параллельным запуском: уникальный индекс операций.
        await db.rollback()
        operation = await operations.active(db)
        message = operations.busy_message(operation) if operation else "Слот операций занят."
        return Busy(message)
    _, latest = await _records(db, record.study_id)
    return await _view(db, record, latest, llm)


async def start_manually(
    db: AsyncSession, account_id: int, llm: LLM | None
) -> Draft | questions.NotReady | Busy:
    """Ручное заполнение без предложений модели; сохранённые строки черновика не стираются."""
    opened = await _open(db, account_id)
    if not isinstance(opened, tuple):
        return opened
    record, version = opened
    # Пользователь сам заполняет критерии к текущей формулировке.
    record.question_version_id = version.id
    record.status = "ready"
    record.saved_at = datetime.now(UTC)
    await db.commit()
    _, latest = await _records(db, record.study_id)
    return await _view(db, record, latest, llm)


async def save(
    db: AsyncSession, account_id: int, fields: CriteriaFields, llm: LLM | None
) -> Draft | Busy | None:
    study = await questions.find_study(db, account_id)
    record, latest = await _records(db, study.id, lock=True) if study else (None, None)
    if record is None:
        return None
    if busy := await operations.busy(db):
        return busy
    record.fields = fields.model_dump()
    record.saved_at = datetime.now(UTC)
    await db.commit()
    return await _view(db, record, latest, llm)


async def confirm(db: AsyncSession, account_id: int, llm: LLM | None) -> int | Blocked | None:
    """Фиксирует черновик как новую версию; повтор без изменений возвращает прежнюю."""
    study = await questions.find_study(db, account_id, lock=True)
    record, latest = await _records(db, study.id) if study else (None, None)
    if study is None or record is None:
        return None
    draft = await _view(db, record, latest, llm)
    if "confirm" not in draft.allowed_actions:
        await db.rollback()
        return Blocked(draft)
    if latest is not None and latest.fields == record.fields:
        number = latest.number
    else:
        number = (latest.number if latest else 0) + 1
        db.add(
            CriteriaVersionRecord(
                study_id=study.id,
                question_version_id=record.question_version_id,
                number=number,
                fields=record.fields,
                created_at=datetime.now(UTC),
            )
        )
    if study.reached_step in ("question", "criteria"):
        study.reached_step = "search"
    await db.commit()
    return number


class SuggestionFailed(Exception):
    """Бюджет попыток исчерпан; `reason` — причина последней попытки."""

    def __init__(self, reason: str, attempts: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.attempts = attempts


async def suggest(llm: LLM, question: str, attempts: int) -> tuple[Completion, int]:
    """Вызывает модель с ограниченным бюджетом попыток; модель не меняется.

    Возвращает ответ и число потраченных попыток.
    """
    reason = ""
    for attempt in range(1, attempts + 1):
        try:
            completion = await llm.complete(
                system=SYSTEM_PROMPT,
                user=f"Исследовательский вопрос:\n{question}",
                schema=CriteriaSuggestion,
            )
            return completion, attempt
        except LLMUnavailable as error:
            reason = str(error)
    raise SuggestionFailed(reason, attempts)


def _suggested_criteria(suggestion: CriteriaSuggestion) -> list[SearchCriterion]:
    return [
        SearchCriterion(
            id=_id("crit"),
            label=item.label,
            terms=item.terms,
            suggested_terms=item.terms,
            origin="suggested",
        )
        for item in suggestion.search_criteria
    ]


def apply_suggestion(
    record: CriteriaDraftRecord, suggestion: CriteriaSuggestion, question_version_id: int
) -> None:
    """Заменяет виды и критерии предложениями модели; параметры пользователя не трогает."""
    gaps = [
        f"Не получено научное название для «{item.name}» — укажите его вручную."
        for item in suggestion.objects
        if not (item.scientific_name or "").strip()
    ]
    if not suggestion.objects:
        gaps.append("Модель не предложила виды животных — добавьте их вручную.")
    if not suggestion.search_criteria:
        gaps.append("Модель не предложила критерии поискового запроса — добавьте их вручную.")
    fields = CriteriaFields.model_validate(record.fields)
    fields.objects = [
        StudyObject(
            id=_id("obj"),
            name=item.name,
            scientific_name=item.scientific_name or "",
            origin="suggested",
            aliases=item.aliases,
            suggested_aliases=item.aliases,
        )
        for item in suggestion.objects
    ]
    fields.search_criteria = _suggested_criteria(suggestion)
    record.fields = fields.model_dump()
    record.question_version_id = question_version_id
    record.gaps = gaps
    record.status = "partial" if gaps else "ready"
    record.saved_at = datetime.now(UTC)


def record_failure(record: CriteriaDraftRecord, reason: str) -> None:
    record.status = "failed"
    record.gaps = [
        f"Предложения модели не получены: {reason}. Заполните критерии вручную "
        "или повторите подготовку на шаге «Вопрос»."
    ]
    record.saved_at = datetime.now(UTC)


async def reformulate(
    db: AsyncSession, account_id: int, llm: LLM | None
) -> list[SearchCriterion] | Busy | Unavailable | None:
    """Новая редакция критериев по вопросу; черновик не меняется до решения пользователя."""
    study = await questions.find_study(db, account_id)
    record, _ = await _records(db, study.id) if study else (None, None)
    if record is None:
        return None
    if busy := await operations.busy(db):
        return busy
    if llm is None:
        return Unavailable(NO_MODEL)
    question = await questions.latest_version(db, record.study_id)
    assert question is not None
    # Транзакция чтения закрывается до вызова модели, чтобы не держать соединение открытым.
    await db.commit()
    # Действие синхронное: одна попытка, чтобы пользователь не ждал весь бюджет повторов.
    try:
        completion, _ = await suggest(llm, question.text, attempts=1)
    except SuggestionFailed as error:
        return Unavailable(f"Новая редакция не получена: {error.reason}.")
    # Редакция не занимает слот и не создаёт операцию; ответившая модель фиксируется в журнале.
    log.info(
        "Новая редакция критериев: модель %s, провайдер %s", completion.model, completion.provider
    )
    return _suggested_criteria(completion.value)
