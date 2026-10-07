"""Карточка статьи и опубликованные показатели работы (#15; spec «Опубликованный показатель»).

Ответ модели проверяется Pydantic-схемой `Card` с показателями `Measure`: модель не задаёт
ID, статусы проверки и актуальность. Документы, показатели и ограничения авторов модель
называет локальными ключами ответа; сервер сверяет их и заменяет своими номерами. Каждая
успешная попытка извлечения пишет новую кандидатную ревизию карточки — машинную, ещё не
проверенную пользователем; ошибка попытки ревизию не пишет, прежняя остаётся. Основание —
цитата и подпись места моделью; `check` оставляет его `pending`, адрес в разобранном документе
и статус привязки задаёт `evidence.verify` (#16).
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass.criteria import CriteriaVersionRecord
from evidence_compass.db import Base

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
OriginKind = Literal["own_result", "secondary_report"]
Setting = Literal["wild", "captive", "unknown"]
SubjectKind = Literal["individual", "group"]
SetAsideKind = Literal["simulation", "raw_data", "figure_only", "other_species", "other"]
# Пользовательская проверка извлечения и серверная проверка привязки основания — раздельно.
ReviewStatus = Literal["unreviewed", "checked", "corrected", "rejected"]
VerificationStatus = Literal["pending", "verified", "needs_review", "rejected"]

MEASURE_KINDS = get_args(MeasureKind)
ORIGIN_KINDS = get_args(OriginKind)
REVIEW_STATUSES = get_args(ReviewStatus)
VERIFICATION_STATUSES = get_args(VerificationStatus)


# ---------- Ответ модели (structured output, ADR 0007) ----------


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: str = Field(description="Ключ документа из запроса, например D1")
    location: str = Field(
        description="Место в документе, как оно размечено в запросе: страница, абзац §N, "
        "таблица и строка"
    )
    text: str = Field(description="Дословный фрагмент документа, без пересказа")


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence: list[Quote]


class AuthorLimitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Локальный ключ ограничения в этом ответе, например L1")
    text: str = Field(description="Ограничение, прямо указанное авторами; не своё замечание")
    scope: str | None = Field(description="К каким результатам или группам оно относится")
    evidence: list[Quote]


class Uncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="Обозначение неопределённости, как в источнике")
    kind: str | None = Field(description="Тип: CI, SD, SE, posterior range, range и т. п.")
    level_percent: float | None
    plus_minus: str | None
    lower: str | None
    upper: str | None
    upper_status: str | None = Field(
        description="Почему граница не указана: not_reported, not_reached; иначе null"
    )


class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SubjectKind
    label: str = Field(description="Обозначение особи или группы, как в источнике")
    sex: str | None
    sample_size: str | None = Field(description="Размер выборки именно этой группы; иначе null")
    sample_size_scope: str | None


class Conditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting: Setting
    text: str


class Measure(BaseModel):
    """Один опубликованный показатель одной особи или группы в одних условиях."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Локальный ключ показателя в этом ответе, например M1")
    species_name_in_source: str
    species_label: str = Field(description="Объект исследования из списка запроса")
    taxonomy_note: str | None
    measure_kind: MeasureKind
    definition: str = Field(description="Определение показателя словами авторов, дословно")
    value_original: str = Field(
        description="Только число как в источнике, без единиц, знаков сравнения и перевода"
    )
    unit_original: str = Field(description="Единицы как в источнике: years, days")
    qualifier: str | None = Field(
        description="Оговорка словами источника: more than, at least, about, censored"
    )
    uncertainty: Uncertainty | None
    subject: Subject
    conditions: Conditions
    method: str | None = Field(description="Метод определения возраста или расчёта авторов")
    origin_kind: OriginKind = Field(
        description="own_result — результат этой работы; secondary_report — пересказ чужого"
    )
    evidence: list[Quote] = Field(description="Основания значения и определения")
    context_evidence: list[Quote] = Field(
        description="Основания методов и условий именно этой особи или группы"
    )
    limitation_keys: list[str]
    related_keys: list[str] = Field(
        description="Показатели того же субъекта или авторские обобщения тех же данных"
    )


class Unresolved(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        description="Что требует решения: разные значения одного результата либо значение, "
        "группу или условия которого нельзя однозначно установить"
    )
    measure_keys: list[str]
    evidence: list[Quote]


class SetAside(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SetAsideKind
    description: str
    evidence: list[Quote]


class Card(BaseModel):
    """Карточка статьи с показателями, как её возвращает модель."""

    model_config = ConfigDict(extra="forbid")

    study_object: str = Field(description="Объект исследования: виды и животные")
    subjects: list[Statement] = Field(description="Особи, группы и выборки с их составом")
    methods: list[Statement]
    results: list[Statement]
    conclusions: list[Statement] = Field(description="Выводы авторов")
    author_limitations: list[AuthorLimitation]
    measures: list[Measure]
    unresolved: list[Unresolved]
    set_aside: list[SetAside] = Field(
        description="Что сознательно не стало показателями: симуляции, исходные массивы, "
        "значения только с графиков, другие виды"
    )


# ---------- Проверка ответа ----------


class Rejected(Exception):
    """Ответ модели ссылается на то, чего нет в запросе или в самом ответе."""


@dataclass(frozen=True)
class Candidate:
    """Проверенная карточка со ссылками сервера: номера вместо ключей модели."""

    content: dict
    measures: list[dict]


def _quotes(quotes: list[Quote], documents: dict[str, int]) -> list[dict]:
    unknown = sorted({quote.document for quote in quotes} - documents.keys())
    if unknown:
        raise Rejected(f"ссылка на неизвестный документ {', '.join(unknown)}")
    return [
        {
            "document_version_id": documents[quote.document],
            "location": quote.location,
            "quote": quote.text,
            "verification_status": "pending",
        }
        for quote in quotes
    ]


def _numbers(keys: list[str], known: dict[str, int], what: str) -> list[int]:
    unknown = sorted(set(keys) - known.keys())
    if unknown:
        raise Rejected(f"ссылка на неизвестный {what} {', '.join(unknown)}")
    return sorted({known[key] for key in keys})


def _unique(keys: list[str], what: str) -> dict[str, int]:
    numbers: dict[str, int] = {}
    for key in keys:
        if key in numbers:
            raise Rejected(f"повторяется ключ {what} {key}")
        numbers[key] = len(numbers) + 1
    return numbers


# Слова определения, однозначно называющие тип показателя (англ. и рус.).
KIND_WORDS: dict[str, re.Pattern] = {
    "mean": re.compile(r"\b(mean|average)\b|средн", re.IGNORECASE),
    "median": re.compile(r"\bmedian\b|медиан", re.IGNORECASE),
    "observed_maximum": re.compile(r"\bmax(imum)?\b|максим", re.IGNORECASE),
}


def _kind_conflict(row: dict) -> dict | None:
    """Среднее, медиана и максимум не взаимозаменяемы: тип, противоречащий определению
    автора, требует решения, а не выбирается молча."""
    kind = row["measure_kind"]
    if kind not in KIND_WORDS:
        return None
    definition = row["content"]["definition"]
    named = [
        other for other, words in KIND_WORDS.items() if other != kind and words.search(definition)
    ]
    if not named:
        return None
    return {
        "description": f"Тип показателя {kind} расходится с определением «{definition}»: "
        f"в нём назван {', '.join(named)}.",
        "measure_numbers": [row["number"]],
        "evidence": row["content"]["evidence"],
    }


def check(card: Card, documents: dict[str, int], objects: dict[str, str]) -> Candidate:
    """Сверяет ключи ответа и переводит их в номера сервера.

    `documents` — ключ документа запроса → ID версии документа; `objects` — любое название
    объекта исследования (в нижнем регистре) → его подпись. Показатель вида вне вопроса,
    строка без числа в значении и показатель без цитаты не теряются молча: они отложены с
    причиной. Без цитаты значение — утверждение модели, а не извлечение из документа.
    """
    set_aside = [
        {
            "kind": item.kind,
            "description": item.description,
            "evidence": _quotes(item.evidence, documents),
        }
        for item in card.set_aside
    ]
    measures = []
    for measure in card.measures:
        summary = (
            f"{measure.species_name_in_source}: {measure.definition} — "
            f"{measure.value_original} {measure.unit_original}".rstrip()
        )
        label = objects.get(measure.species_label.strip().casefold())
        if not any(char.isdigit() for char in measure.value_original):
            # «null», «N/A», «сведений нет» — не опубликованный показатель.
            reason = ("other", f"Значение не указано числом: {summary}")
        elif not measure.evidence:
            reason = ("other", f"Нет цитаты из документа: {summary}")
        elif objects and label is None:
            reason = ("other_species", summary)
        else:
            measures.append(
                measure.model_copy(update={"species_label": label or measure.species_label})
            )
            continue
        kind, description = reason
        set_aside.append(
            {
                "kind": kind,
                "description": description,
                "evidence": _quotes(measure.evidence, documents),
            }
        )
    limitations = _unique([item.key for item in card.author_limitations], "ограничения")
    numbers = _unique([measure.key for measure in measures], "показателя")
    # Отложенный показатель не строка карточки: ссылки на него не переносятся.
    set_aside_keys = {measure.key for measure in card.measures} - numbers.keys()

    def related(keys: list[str]) -> list[int]:
        return _numbers([key for key in keys if key not in set_aside_keys], numbers, "показатель")

    rows = []
    for measure in measures:
        evidence = _quotes(measure.evidence, documents)
        rows.append(
            {
                "number": numbers[measure.key],
                "measure_kind": measure.measure_kind,
                "origin_kind": measure.origin_kind,
                "verification_status": "pending",
                "content": {
                    **measure.model_dump(
                        exclude={
                            "key",
                            "measure_kind",
                            "origin_kind",
                            "evidence",
                            "context_evidence",
                            "limitation_keys",
                            "related_keys",
                        }
                    ),
                    "evidence": evidence,
                    "context_evidence": _quotes(measure.context_evidence, documents),
                    "author_limitation_numbers": _numbers(
                        measure.limitation_keys, limitations, "ограничение"
                    ),
                    "related_measure_numbers": related(measure.related_keys),
                },
            }
        )

    def statements(items: list[Statement]) -> list[dict]:
        return [
            {"text": item.text, "evidence": _quotes(item.evidence, documents)} for item in items
        ]

    reported = [
        {
            "description": item.description,
            "measure_numbers": related(item.measure_keys),
            "evidence": _quotes(item.evidence, documents),
        }
        for item in card.unresolved
    ]
    # Показатель, уже отмеченный моделью, второй раз не описывается.
    flagged = {n for item in reported for n in item["measure_numbers"]}
    content = {
        "study_object": card.study_object,
        "subjects": statements(card.subjects),
        "methods": statements(card.methods),
        "results": statements(card.results),
        "conclusions": statements(card.conclusions),
        "author_limitations": [
            {
                "number": limitations[item.key],
                "text": item.text,
                "scope": item.scope,
                "evidence": _quotes(item.evidence, documents),
            }
            for item in card.author_limitations
        ],
        "unresolved": reported
        + [
            problem
            for row in rows
            if row["number"] not in flagged and (problem := _kind_conflict(row))
        ],
        "set_aside": set_aside,
    }
    return Candidate(content, rows)


# ---------- Хранение ----------


def _known(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class CardRevisionRecord(Base):
    """Неизменяемый снимок карточки работы по версиям критериев и документов."""

    __tablename__ = "card_revisions"
    __table_args__ = (
        UniqueConstraint("publication_id", "number", name="card_revisions_number"),
        # Одна попытка — одна ревизия: повтор после сбоя worker не дублирует её.
        UniqueConstraint("item_id", name="card_revisions_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    number: Mapped[int]
    item_id: Mapped[int] = mapped_column(ForeignKey("extraction_items.id", ondelete="CASCADE"))
    criteria_version_id: Mapped[int | None] = mapped_column(ForeignKey(CriteriaVersionRecord.id))
    # [{document_id, version}] — документы, по которым извлечена ревизия.
    document_versions: Mapped[list[dict]] = mapped_column(JSONB)
    content: Mapped[dict] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MeasureRecord(Base):
    """Версия опубликованного показателя в ревизии карточки."""

    __tablename__ = "measures"
    __table_args__ = (
        UniqueConstraint("revision_id", "number", name="measures_number"),
        CheckConstraint(_known("measure_kind", MEASURE_KINDS), name="measure_kind_known"),
        CheckConstraint(_known("origin_kind", ORIGIN_KINDS), name="origin_kind_known"),
        CheckConstraint(_known("review_status", REVIEW_STATUSES), name="review_status_known"),
        CheckConstraint(
            _known("verification_status", VERIFICATION_STATUSES), name="verification_status_known"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("card_revisions.id", ondelete="CASCADE"))
    number: Mapped[int]
    measure_kind: Mapped[str] = mapped_column(String(32))
    origin_kind: Mapped[str] = mapped_column(String(32))
    # Машинная ревизия не может сама назначить `checked`.
    review_status: Mapped[str] = mapped_column(String(16), default="unreviewed")
    verification_status: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict] = mapped_column(JSONB)


async def add(
    db: AsyncSession,
    *,
    publication_id: int,
    number: int,
    item_id: int,
    criteria_version_id: int | None,
    document_versions: list[dict],
    candidate: Candidate,
    model: str,
    provider: str | None,
) -> CardRevisionRecord:
    """Добавляет кандидатную ревизию в текущую транзакцию: фиксирует её вызывающий
    вместе с итогом работы."""
    revision = CardRevisionRecord(
        publication_id=publication_id,
        number=number,
        item_id=item_id,
        criteria_version_id=criteria_version_id,
        document_versions=document_versions,
        content=candidate.content,
        model=model,
        provider=provider,
        created_at=datetime.now(UTC),
    )
    db.add(revision)
    await db.flush()
    db.add_all(
        MeasureRecord(revision_id=revision.id, review_status="unreviewed", **row)
        for row in candidate.measures
    )
    return revision


async def next_number(db: AsyncSession, publication_id: int) -> int:
    latest = await db.scalar(
        select(CardRevisionRecord.number)
        .where(CardRevisionRecord.publication_id == publication_id)
        .order_by(CardRevisionRecord.number.desc())
        .limit(1)
    )
    return (latest or 0) + 1


# ---------- Чтение ----------


@dataclass(frozen=True)
class MeasureVersion:
    number: int
    measure_kind: MeasureKind
    origin_kind: OriginKind
    review_status: ReviewStatus
    verification_status: VerificationStatus
    content: dict


@dataclass(frozen=True)
class CardRevision:
    number: int
    criteria_version_id: int | None
    document_versions: list[dict]
    content: dict
    measures: list[MeasureVersion]
    model: str
    provider: str | None


async def history(db: AsyncSession, publication_id: int) -> list[CardRevision]:
    """Ревизии карточки работы по порядку; актуальная — последняя."""
    revisions = await db.scalars(
        select(CardRevisionRecord)
        .where(CardRevisionRecord.publication_id == publication_id)
        .order_by(CardRevisionRecord.number)
    )
    found = []
    for revision in revisions:
        measures = await db.scalars(
            select(MeasureRecord)
            .where(MeasureRecord.revision_id == revision.id)
            .order_by(MeasureRecord.number)
        )
        found.append(
            CardRevision(
                number=revision.number,
                criteria_version_id=revision.criteria_version_id,
                document_versions=revision.document_versions,
                content=revision.content,
                measures=[
                    MeasureVersion(
                        number=row.number,
                        measure_kind=row.measure_kind,
                        origin_kind=row.origin_kind,
                        review_status=row.review_status,
                        verification_status=row.verification_status,
                        content=row.content,
                    )
                    for row in measures
                ],
                model=revision.model,
                provider=revision.provider,
            )
        )
    return found
