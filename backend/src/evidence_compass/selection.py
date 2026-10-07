"""Отбор публикаций: решение о соответствии критериям, выбор до восьми работ, связи версий (#12).

Все критерии выражены в поисковых запросах, поэтому система не выносит решение о соответствии
сама: найденная запись ждёт решения пользователя. Выбор такой записи для извлечения включает её
от имени пользователя. Решения не перезаписываются: каждое — новая строка с автором, причиной и
версией критериев. Одна запись объединяет выдачу ресурсов только по совпадающему DOI, PMID или
PMCID; препринт и журнальная версия связываются лишь явным решением пользователя.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass import operations, questions, search
from evidence_compass.accounts import Account
from evidence_compass.criteria import CriteriaVersionRecord
from evidence_compass.db import Base
from evidence_compass.operations import Busy
from evidence_compass.sources import FoundRecord, Source

Decision = Literal["included", "excluded", "needs_review"]
Author = Literal["system", "user"]
VersionType = Literal["journal", "preprint"]
Provenance = Literal["pubmed", "europe_pmc", "manual"]
VersionLink = Literal["none", "confirmed", "needs_review"]
OpenAccess = Literal["open", "closed", "unknown"]

LIMIT = 8
DECISION_LABELS = {
    "included": "Соответствует: да",
    "excluded": "Соответствует: нет",
    "needs_review": "Соответствует: не решено",
}
PROVENANCE_ORDER = ("manual", "pubmed", "europe_pmc")
# Сходство названий только предлагает связь версий на проверку пользователю.
SIMILAR_TITLES = 0.9

SELECTED_FOR_EXTRACTION = "Отмечена соответствующей пользователем."
ADDED_MANUALLY = "Добавлено вручную: проверьте соответствие критериям."
SIMILAR_TITLE_BASIS = "Похожие названия препринта и журнальной версии; связь не подтверждена."
EXCLUDED = "Исключённую работу выбрать нельзя: сначала измените решение."
OTHER_VERSION = "Уже выбрана другая версия этой работы."
FULL = f"Выбрано {LIMIT} из {LIMIT}: снимите выбор с другой работы."
NOTHING_SELECTED = "Выберите от одной до восьми работ."
NOTHING_TO_SELECT = "Нет работ для выбора: измените решение или добавьте работу вручную."


def _known(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class PublicationRecord(Base):
    """Одна библиографическая запись в исследовании; выбор для извлечения относится к нему."""

    __tablename__ = "publications"
    # DOI и PMID не уникальны: записи с противоречащими идентификаторами хранятся раздельно.
    __table_args__ = (
        CheckConstraint(_known("version_type", ("journal", "preprint")), name="version_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None]
    doi: Mapped[str | None] = mapped_column(Text)
    pmid: Mapped[str | None] = mapped_column(String(32))
    pmcid: Mapped[str | None] = mapped_column(String(32))
    abstract: Mapped[str | None] = mapped_column(Text)
    version_type: Mapped[str] = mapped_column(String(16))
    # Открытый текст по сведениям источников, не проверенный загрузкой; None — нет сведений.
    open_access: Mapped[bool | None]
    # Постоянная метка: найденная позже поиском работа не становится успехом поиска.
    added_manually: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationSourceRecord(Base):
    """Происхождение записи: запись выдачи конкретного запуска или ручное добавление."""

    __tablename__ = "publication_sources"
    __table_args__ = (
        CheckConstraint(_known("resource", PROVENANCE_ORDER), name="resource_known"),
        UniqueConstraint("publication_id", "search_record_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    resource: Mapped[str] = mapped_column(String(16))
    identifier: Mapped[str] = mapped_column(Text)
    search_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_records.id", ondelete="CASCADE")
    )


class PublicationDecisionRecord(Base):
    """Решение о соответствии критериям; действует последнее, прежние остаются историей."""

    __tablename__ = "publication_decisions"
    __table_args__ = (
        CheckConstraint(_known("decision", tuple(DECISION_LABELS)), name="decision_known"),
        CheckConstraint(_known("author", ("system", "user")), name="author_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(8))
    # Пользователь, принявший решение; у решения системы — нет.
    account_id: Mapped[int | None] = mapped_column(ForeignKey(Account.id))
    criteria_version_id: Mapped[int] = mapped_column(ForeignKey("criteria_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationRelationRecord(Base):
    """Связь версий одной работы: предложение системы или решение пользователя."""

    __tablename__ = "publication_relations"
    __table_args__ = (
        UniqueConstraint("first_id", "second_id"),
        CheckConstraint("first_id < second_id", name="ordered_pair"),
        CheckConstraint(
            _known("status", ("needs_review", "confirmed", "rejected")), name="status_known"
        ),
        CheckConstraint(_known("author", ("system", "user")), name="author_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    first_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    second_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    basis: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(8))
    account_id: Mapped[int | None] = mapped_column(ForeignKey(Account.id))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class DecisionView:
    decision: Decision
    reason: str
    author: Author
    criteria_version: int


@dataclass(frozen=True)
class SourceView:
    resource: Provenance
    identifier: str
    url: str | None


@dataclass(frozen=True)
class RelatedView:
    id: int
    title: str
    version_type: VersionType
    link: VersionLink


@dataclass(frozen=True)
class Row:
    id: int
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
    select_blocked_reason: str | None


@dataclass(frozen=True)
class Card:
    row: Row
    abstract: str | None
    sources: list[SourceView]
    decision_record: DecisionView
    related_versions: list[RelatedView]


@dataclass(frozen=True)
class Selection:
    publications: list[Row]
    selected_count: int
    proceed_blocked_reason: str | None


@dataclass(frozen=True)
class Refused:
    """Изменение отбора отклонено; `target` — id записи или «selection»."""

    target: str
    message: str


@dataclass(frozen=True)
class Candidate:
    record: FoundRecord
    already_listed: bool


class NotFound(Exception):
    """Публикации нет в отборе текущего исследования."""


# ---------- Происхождение и версии ----------


def version_type(record: FoundRecord) -> VersionType:
    preprint = record.source_id.startswith("PPR:") or any(
        kind.lower() == "preprint" for kind in record.publication_types
    )
    return "preprint" if preprint else "journal"


def _url(resource: str, identifier: str) -> str | None:
    match resource:
        case "pubmed":
            return f"https://pubmed.ncbi.nlm.nih.gov/{identifier}/"
        case "europe_pmc":
            source, _, number = identifier.partition(":")
            return f"https://europepmc.org/article/{source}/{number}"
    return None


def _open_access(value: bool | None) -> OpenAccess:
    return "unknown" if value is None else "open" if value else "closed"


def _merge_open_access(publication: PublicationRecord, record: FoundRecord) -> None:
    """Открытый текст есть, если о нём сообщил хотя бы один источник."""
    if record.open_access:
        publication.open_access = True
    elif publication.open_access is None:
        publication.open_access = record.open_access


def _normalized(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", title.lower()).split())


def _similar(first: str, second: str) -> bool:
    return SequenceMatcher(None, _normalized(first), _normalized(second)).ratio() >= SIMILAR_TITLES


# ---------- Чтение ----------


async def _latest_completed_run(db: AsyncSession, study_id: int) -> search.SearchRunRecord | None:
    return await db.scalar(
        select(search.SearchRunRecord)
        .join(operations.OperationRecord)
        .where(
            search.SearchRunRecord.study_id == study_id,
            operations.OperationRecord.status == "completed",
        )
        .order_by(search.SearchRunRecord.number.desc())
        .limit(1)
    )


async def _listed(db: AsyncSession, study_id: int) -> list[PublicationRecord]:
    """Записи отбора: найденные последним завершённым поиском, добавленные вручную и те, по
    которым пользователь уже принимал решение, — они не исчезают молча после нового поиска."""
    run = await _latest_completed_run(db, study_id)
    decided = select(PublicationDecisionRecord.publication_id).where(
        PublicationDecisionRecord.author == "user"
    )
    found = (
        select(PublicationSourceRecord.publication_id)
        .join(search.SearchRecordRecord)
        .join(search.SearchSourceRecord)
        .where(search.SearchSourceRecord.run_id == (run.id if run else None))
    )
    rows = await db.scalars(
        select(PublicationRecord)
        .where(
            PublicationRecord.study_id == study_id,
            or_(
                PublicationRecord.added_manually,
                PublicationRecord.id.in_(found),
                PublicationRecord.id.in_(decided),
            ),
        )
        .order_by(PublicationRecord.id)
    )
    return list(rows)


async def _current_decision(
    db: AsyncSession, publication_id: int
) -> PublicationDecisionRecord | None:
    return await db.scalar(
        select(PublicationDecisionRecord)
        .where(PublicationDecisionRecord.publication_id == publication_id)
        .order_by(PublicationDecisionRecord.id.desc())
        .limit(1)
    )


async def _decision(db: AsyncSession, publication_id: int) -> PublicationDecisionRecord:
    record = await _current_decision(db, publication_id)
    assert record is not None, "у публикации всегда есть решение"
    return record


async def _relations(db: AsyncSession, publication_id: int) -> list[PublicationRelationRecord]:
    rows = await db.scalars(
        select(PublicationRelationRecord)
        .where(
            or_(
                PublicationRelationRecord.first_id == publication_id,
                PublicationRelationRecord.second_id == publication_id,
            ),
            PublicationRelationRecord.status != "rejected",
        )
        .order_by(PublicationRelationRecord.id)
    )
    return list(rows)


def _other(relation: PublicationRelationRecord, publication_id: int) -> int:
    return relation.second_id if relation.first_id == publication_id else relation.first_id


async def provenance(db: AsyncSession, publication: PublicationRecord) -> list[Provenance]:
    rows = await _sources(db, publication.id)
    found = {row.resource for row in rows}
    return [resource for resource in PROVENANCE_ORDER if resource in found]


async def _sources(db: AsyncSession, publication_id: int) -> list[PublicationSourceRecord]:
    rows = await db.scalars(
        select(PublicationSourceRecord)
        .where(PublicationSourceRecord.publication_id == publication_id)
        .order_by(PublicationSourceRecord.id)
    )
    return list(rows)


class _Context:
    """Общие для строк отбора сведения: число выбранных и запрет поиска."""

    def __init__(self, selected: set[int], search_blocked: str | None) -> None:
        self.selected = selected
        self.search_blocked = search_blocked


async def _context(db: AsyncSession, account_id: int, study_id: int) -> _Context:
    run = await search.get(db, account_id)
    selected = await db.scalars(
        select(PublicationRecord.id).where(
            PublicationRecord.study_id == study_id, PublicationRecord.selected
        )
    )
    return _Context(set(selected), run.selection_blocked_reason if run else None)


async def _row(db: AsyncSession, publication: PublicationRecord, context: _Context) -> Row:
    decision = await _decision(db, publication.id)
    relations = await _relations(db, publication.id)
    confirmed = [r for r in relations if r.status == "confirmed"]
    link: VersionLink = "confirmed" if confirmed else "needs_review" if relations else "none"
    return Row(
        id=publication.id,
        title=publication.title,
        authors=publication.authors,
        doi=publication.doi,
        year=publication.year,
        version_type=publication.version_type,
        provenance=await provenance(db, publication),
        decision=decision.decision,
        versions=1 + len(relations),
        version_link=link,
        open_access=_open_access(publication.open_access),
        selected=publication.selected,
        select_blocked_reason=_select_blocked(publication, decision, confirmed, context),
    )


def _select_blocked(
    publication: PublicationRecord,
    decision: PublicationDecisionRecord,
    confirmed: list[PublicationRelationRecord],
    context: _Context,
) -> str | None:
    if publication.selected:
        return None
    if context.search_blocked:
        return context.search_blocked
    if decision.decision == "excluded":
        return EXCLUDED
    if any(_other(relation, publication.id) in context.selected for relation in confirmed):
        return OTHER_VERSION
    return FULL if len(context.selected) >= LIMIT else None


async def _criteria_number(db: AsyncSession, version_id: int) -> int:
    version = await db.get(CriteriaVersionRecord, version_id)
    assert version is not None
    return version.number


async def _card(db: AsyncSession, publication: PublicationRecord, context: _Context) -> Card:
    decision = await _decision(db, publication.id)
    related = []
    for relation in await _relations(db, publication.id):
        other = await db.get(PublicationRecord, _other(relation, publication.id))
        assert other is not None
        related.append(RelatedView(other.id, other.title, other.version_type, relation.status))
    seen: dict[tuple[str, str], SourceView] = {}
    for source in await _sources(db, publication.id):
        key = (source.resource, source.identifier)
        seen.setdefault(key, SourceView(*key, _url(*key)))
    return Card(
        row=await _row(db, publication, context),
        abstract=publication.abstract,
        sources=sorted(seen.values(), key=lambda s: PROVENANCE_ORDER.index(s.resource)),
        decision_record=DecisionView(
            decision.decision,
            decision.reason,
            decision.author,
            await _criteria_number(db, decision.criteria_version_id),
        ),
        related_versions=related,
    )


async def _selection(db: AsyncSession, account_id: int, study_id: int) -> Selection:
    context = await _context(db, account_id, study_id)
    rows = [await _row(db, item, context) for item in await _listed(db, study_id)]
    count = sum(row.selected for row in rows)
    if context.search_blocked:
        blocked: str | None = context.search_blocked
    elif count:
        blocked = None
    elif all(row.decision == "excluded" for row in rows):
        blocked = NOTHING_TO_SELECT
    else:
        blocked = NOTHING_SELECTED
    return Selection(rows, count, blocked)


async def get(db: AsyncSession, account_id: int) -> Selection | None:
    """Отбор текущего исследования; None — критерии ещё не подтверждались."""
    study = await questions.find_study(db, account_id)
    if study is None or await search.get(db, account_id) is None:
        return None
    return await _selection(db, account_id, study.id)


async def _find(
    db: AsyncSession, account_id: int, publication_id: int, *, listed: bool = False
) -> PublicationRecord:
    """Публикация исследования; `listed` — только из текущего отбора, с блокировкой строки."""
    study = await questions.find_study(db, account_id)
    publication = await db.get(PublicationRecord, publication_id, with_for_update=listed)
    if study is None or publication is None or publication.study_id != study.id:
        raise NotFound
    if listed and publication.id not in {item.id for item in await _listed(db, study.id)}:
        raise NotFound
    return publication


async def card(db: AsyncSession, account_id: int, publication_id: int) -> Card:
    publication = await _find(db, account_id, publication_id)
    return await _card(db, publication, await _context(db, account_id, publication.study_id))


# ---------- Изменения ----------


async def _changeable(db: AsyncSession, account_id: int) -> Busy | Refused | None:
    """Изменять отбор можно при свободном слоте и завершённом поиске последней версии."""
    if busy := await operations.busy(db):
        return busy
    run = await search.get(db, account_id)
    if run and run.selection_blocked_reason:
        await db.rollback()
        return Refused("selection", run.selection_blocked_reason)
    return None


async def _searched_version(db: AsyncSession, study_id: int) -> int:
    # Изменения разрешены только после завершённого поиска последней версии (`_changeable`).
    run = await _latest_completed_run(db, study_id)
    assert run is not None
    return run.criteria_version_id


async def _decide(
    db: AsyncSession,
    publication: PublicationRecord,
    decision: Decision,
    reason: str,
    author: Author,
    account_id: int | None,
    criteria_version_id: int,
) -> None:
    db.add(
        PublicationDecisionRecord(
            publication_id=publication.id,
            decision=decision,
            reason=reason,
            author=author,
            account_id=account_id,
            criteria_version_id=criteria_version_id,
            created_at=datetime.now(UTC),
        )
    )
    if decision != "included":
        publication.selected = False


async def change_decision(
    db: AsyncSession, account_id: int, publication_id: int, decision: Decision, reason: str
) -> Card | Busy | Refused:
    publication = await _find(db, account_id, publication_id, listed=True)
    if refused := await _changeable(db, account_id):
        return refused
    version = await _searched_version(db, publication.study_id)
    await _decide(db, publication, decision, reason, "user", account_id, version)
    await db.commit()
    return await card(db, account_id, publication_id)


async def change_selected(
    db: AsyncSession, account_id: int, publication_id: int, selected: bool
) -> Selection | Busy | Refused:
    # Блокировка исследования упорядочивает параллельные выборы: лимит не превышается.
    study = await questions.find_study(db, account_id, lock=True)
    publication = await _find(db, account_id, publication_id, listed=True)
    assert study is not None
    if refused := await _changeable(db, account_id):
        return refused
    if selected and not publication.selected:
        row = await _row(db, publication, await _context(db, account_id, study.id))
        if row.select_blocked_reason:
            await db.rollback()
            return Refused(str(publication_id), row.select_blocked_reason)
        if row.decision == "needs_review":
            version = await _searched_version(db, study.id)
            await _decide(
                db, publication, "included", SELECTED_FOR_EXTRACTION, "user", account_id, version
            )
    publication.selected = selected
    await db.commit()
    return await _selection(db, account_id, study.id)


async def resolve_version_link(
    db: AsyncSession, account_id: int, publication_id: int, related_id: int, same_work: bool
) -> Card | Busy | Refused:
    if publication_id == related_id:
        return Refused(str(publication_id), "Публикацию нельзя связать с самой собой.")
    # Строки блокируются в порядке id: встречные запросы по одной паре не взаимоблокируются.
    locked = {
        key: await _find(db, account_id, key, listed=True)
        for key in sorted((publication_id, related_id))
    }
    publication, related = locked[publication_id], locked[related_id]
    if refused := await _changeable(db, account_id):
        return refused
    first, second = sorted((publication.id, related.id))
    relation = await db.scalar(
        select(PublicationRelationRecord).where(
            PublicationRelationRecord.first_id == first,
            PublicationRelationRecord.second_id == second,
        )
    )
    if relation is None:
        relation = PublicationRelationRecord(first_id=first, second_id=second)
        db.add(relation)
    relation.status = "confirmed" if same_work else "rejected"
    relation.basis = "Решение пользователя."
    relation.author, relation.account_id = "user", account_id
    relation.decided_at = datetime.now(UTC)
    if same_work and publication.selected and related.selected:
        # Одна работа не считается двумя: журнальная версия предпочтительнее препринта.
        dropped = related if related.version_type == "preprint" else publication
        dropped.selected = False
    await db.commit()
    return await card(db, account_id, publication_id)


async def _suggest_links(db: AsyncSession, study_id: int) -> None:
    """Предлагает связь препринта и журнальной версии с похожими названиями."""
    listed = await _listed(db, study_id)
    known = {
        (r.first_id, r.second_id)
        for r in await db.scalars(
            select(PublicationRelationRecord).where(
                PublicationRelationRecord.first_id.in_([item.id for item in listed])
            )
        )
    }
    preprints = [item for item in listed if item.version_type == "preprint"]
    journals = [item for item in listed if item.version_type == "journal"]
    for preprint in preprints:
        for journal in journals:
            first, second = sorted((preprint.id, journal.id))
            if (first, second) in known or not _similar(preprint.title, journal.title):
                continue
            known.add((first, second))
            db.add(
                PublicationRelationRecord(
                    first_id=first,
                    second_id=second,
                    status="needs_review",
                    basis=SIMILAR_TITLE_BASIS,
                    author="system",
                    decided_at=datetime.now(UTC),
                )
            )


IDENTIFIERS = ("doi", "pmid", "pmcid")


def _exact_title(title: str) -> str:
    return " ".join(title.casefold().split())


def _conflicts(publication: PublicationRecord, record: FoundRecord) -> bool:
    return any(
        getattr(publication, name)
        and getattr(record, name)
        and getattr(publication, name) != getattr(record, name)
        for name in IDENTIFIERS
    )


def _shares_identifier(publication: PublicationRecord, record: FoundRecord) -> bool:
    return any(
        getattr(record, name) and getattr(publication, name) == getattr(record, name)
        for name in IDENTIFIERS
    )


def _split_identifiers(publication: PublicationRecord, record: FoundRecord) -> bool:
    """Запись PubMed только с PMID и запись Europe PMC только с DOI — одна работа, если
    совпадают название, год и тип версии. Препринт с журнальной версией так не сливается:
    их связь — решение пользователя."""
    if (publication.doi and publication.pmid) or (record.doi and record.pmid):
        return False
    if not ((publication.pmid and record.doi) or (publication.doi and record.pmid)):
        return False
    return (
        _exact_title(publication.title) == _exact_title(record.title)
        and (publication.year is None or record.year is None or publication.year == record.year)
        and publication.version_type == version_type(record)
    )


async def _same_record(
    db: AsyncSession, study_id: int, record: FoundRecord
) -> PublicationRecord | None:
    """Та же запись по общему DOI, PMID или PMCID без противоречий в остальных.

    Противоречащий идентификатор запрещает объединение, даже если другой совпал.
    Похожее название записи не объединяет.
    """
    matches = [
        getattr(PublicationRecord, name) == getattr(record, name)
        for name in IDENTIFIERS
        if getattr(record, name)
    ]
    if bool(record.doi) != bool(record.pmid):
        # Кандидаты для записи с одним идентификатором; проверка — `_split_identifiers`.
        matches.append(
            PublicationRecord.year.is_(None)
            if record.year is None
            else or_(PublicationRecord.year == record.year, PublicationRecord.year.is_(None))
        )
    if not matches:
        return None
    candidates = list(
        await db.scalars(
            select(PublicationRecord)
            .where(PublicationRecord.study_id == study_id, or_(*matches))
            .order_by(PublicationRecord.id)
        )
    )
    allowed = [item for item in candidates if not _conflicts(item, record)]
    return next(
        (item for item in allowed if _shares_identifier(item, record)),
        next((item for item in allowed if _split_identifiers(item, record)), None),
    )


def _new_publication(study_id: int, record: FoundRecord, *, manual: bool) -> PublicationRecord:
    return PublicationRecord(
        study_id=study_id,
        title=record.title,
        authors=record.authors,
        year=record.year,
        doi=record.doi,
        pmid=record.pmid,
        pmcid=record.pmcid,
        abstract=record.abstract,
        version_type=version_type(record),
        open_access=record.open_access,
        added_manually=manual,
        selected=False,
        created_at=datetime.now(UTC),
    )


async def _fill_missing(
    db: AsyncSession, publication: PublicationRecord, record: FoundRecord
) -> None:
    """Дополняет недостающие сведения из другого ресурса, не меняя имеющиеся.

    Идентификатор, уже принадлежащий другой записи исследования, не переносится: записи
    не сливаются по частичному совпадению.
    """
    for name in ("abstract", "year"):
        if getattr(publication, name) is None and getattr(record, name) is not None:
            setattr(publication, name, getattr(record, name))
    for column in (PublicationRecord.doi, PublicationRecord.pmid, PublicationRecord.pmcid):
        value = getattr(record, column.key)
        if getattr(publication, column.key) is not None or value is None:
            continue
        taken = await db.scalar(
            select(PublicationRecord.id).where(
                PublicationRecord.study_id == publication.study_id, column == value
            )
        )
        if taken is None:
            setattr(publication, column.key, value)


async def absorb(db: AsyncSession, run: search.SearchRunRecord) -> None:
    """Переносит выдачу завершённого запуска в отбор; вызывается в транзакции завершения.

    Решение по прежней версии критериев не переносится как подтверждённое: запись снова
    требует решения, а прежнее решение остаётся в истории.
    """
    version = await db.get(CriteriaVersionRecord, run.criteria_version_id)
    assert version is not None
    found = await db.scalars(
        select(search.SearchRecordRecord)
        .join(search.SearchSourceRecord)
        .where(search.SearchSourceRecord.run_id == run.id)
        .order_by(search.SearchSourceRecord.id, search.SearchRecordRecord.position)
    )
    seen: set[int] = set()
    for item in found:
        record = FoundRecord(
            source_id=item.source_record_id,
            title=item.title,
            authors=item.authors,
            year=item.year,
            doi=item.doi,
            pmid=item.pmid,
            pmcid=item.pmcid,
            abstract=item.abstract,
            publication_types=item.publication_types,
            open_access=item.open_access,
        )
        publication = await _same_record(db, run.study_id, record)
        if publication is None:
            publication = _new_publication(run.study_id, record, manual=False)
            db.add(publication)
            await db.flush()
        else:
            await _fill_missing(db, publication, record)
            _merge_open_access(publication, record)
        source = await db.get(search.SearchSourceRecord, item.source_id)
        assert source is not None
        db.add(
            PublicationSourceRecord(
                publication_id=publication.id,
                resource=source.resource,
                identifier=item.source_record_id,
                search_record_id=item.id,
            )
        )
        if publication.id not in seen:
            seen.add(publication.id)
            await _recheck(db, publication, version)
        await db.flush()
    # Добавленные вручную и решённые пользователем записи, которых нет в выдаче, остаются в
    # отборе и тоже перепроверяются по новой версии критериев.
    for publication in await _listed(db, run.study_id):
        if publication.id not in seen:
            await _recheck(db, publication, version)
    await _suggest_links(db, run.study_id)


async def _recheck(
    db: AsyncSession, publication: PublicationRecord, version: CriteriaVersionRecord
) -> None:
    current = await _current_decision(db, publication.id)
    if current is not None and current.criteria_version_id == version.id:
        # Повтор поиска той же версии критериев сохраняет решение.
        return
    if current is not None and current.author == "user":
        previous = await _criteria_number(db, current.criteria_version_id)
        reason = (
            f"Критерии изменились (версия {previous} → {version.number}): прежнее решение "
            f"«{DECISION_LABELS[current.decision]}» требует повторной проверки."
        )
    else:
        reason = (
            f"Найдена поиском по версии критериев {version.number}; "
            "соответствие критериям не проверено."
        )
    await _decide(db, publication, "needs_review", reason, "system", None, version.id)


# ---------- Ручное добавление ----------

_DOI = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)


def lookup_query(text: str) -> str:
    """Запрос Europe PMC по DOI, PMID или названию (PubMed индексируется как SRC:MED)."""
    text = text.strip()
    if doi := _DOI.match(text):
        return f'DOI:"{doi.group(1).lower()}"'
    if text.isdigit():
        return f"EXT_ID:{text} AND SRC:MED"
    return 'TITLE:"' + text.replace('"', " ") + '"'


LOOKUP_LIMIT = 5


async def lookup(
    db: AsyncSession, account_id: int, source: Source, text: str
) -> list[Candidate] | None:
    """Кандидаты для ручного добавления; None — критерии ещё не подтверждались."""
    study = await questions.find_study(db, account_id)
    if study is None or await search.get(db, account_id) is None:
        return None
    if len(text.strip()) < 3:
        return []
    found = await source.search(lookup_query(text), LOOKUP_LIMIT)
    listed = {item.id for item in await _listed(db, study.id)}
    candidates = []
    for record in found.records:
        same = await _same_record(db, study.id, record)
        candidates.append(Candidate(record, same is not None and same.id in listed))
    return candidates


async def add_manually(
    db: AsyncSession, account_id: int, source: Source, doi: str | None, pmid: str | None
) -> Row | Busy | Refused | None:
    """Добавляет запись по DOI или PMID; сведения берутся из Europe PMC, а не от клиента.

    None — критерии не подтверждались или запись не найдена.
    """
    if await search.get(db, account_id) is None:
        return None
    if not (doi or pmid):
        return Refused("selection", "Укажите DOI или PMID.")
    # Запрос к ресурсу идёт вне транзакции; слот и дубликаты проверяются после ответа.
    await db.rollback()
    found = await source.search(lookup_query(doi or pmid or ""), 1)
    if not found.records:
        return None
    record = found.records[0]
    study = await questions.find_study(db, account_id, lock=True)
    assert study is not None
    if refused := await _changeable(db, account_id):
        return refused
    existing = await _same_record(db, study.id, record)
    if existing is not None and existing.id in {item.id for item in await _listed(db, study.id)}:
        refused = Refused(str(existing.id), "Эта работа уже есть в отборе.")
        await db.rollback()
        return refused
    if existing is None:
        publication = _new_publication(study.id, record, manual=True)
        db.add(publication)
        await db.flush()
    else:
        # Работа из прежнего поиска возвращается в отбор с меткой ручного добавления.
        publication = existing
        publication.added_manually = True
    db.add(
        PublicationSourceRecord(
            publication_id=publication.id,
            resource="manual",
            identifier=record.doi or record.pmid or record.source_id,
        )
    )
    version = await _searched_version(db, study.id)
    await _decide(db, publication, "needs_review", ADDED_MANUALLY, "user", account_id, version)
    await db.flush()
    await _suggest_links(db, study.id)
    await db.commit()
    return await _row(db, publication, await _context(db, account_id, study.id))


# ---------- Переход к документам ----------


async def proceed(db: AsyncSession, account_id: int) -> Refused | None:
    """Открывает шаг «Подготовка»; отказ — причина, по которой переход недоступен."""
    study = await questions.find_study(db, account_id, lock=True)
    assert study is not None, "вызывается после проверки подтверждённых критериев"
    current = await _selection(db, account_id, study.id)
    if current.proceed_blocked_reason:
        await db.rollback()
        return Refused("selection", current.proceed_blocked_reason)
    if study.reached_step in ("question", "criteria", "search", "selection"):
        study.reached_step = "preparation"
    await db.commit()
    return None
