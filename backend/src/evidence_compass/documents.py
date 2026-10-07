"""Документы выбранных работ: получение, неизменяемые версии, идентичность и разбор (#13).

Документ — основной текст или приложение публикации; каждое получение и каждая ручная
загрузка создают новую неизменяемую версию с SHA-256 (ADR 0004). Получение выполняет worker
вне общего слота операций: ожидание файла не занимает обработку. Статусы получения,
идентичности и разбора хранятся раздельно; строка таблицы сводит их в один текстовый статус
(shape §3.5), не пряча `partial`, `needs_review` и недоступность за «Готов».
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass import operations, questions
from evidence_compass.accounts import Account
from evidence_compass.db import Base
from evidence_compass.operations import Busy
from evidence_compass.parsing import IdentityStatus, Parsed, Work, identity, parse
from evidence_compass.retrieval import NotObtained, Retriever, supplements_url
from evidence_compass.selection import PublicationRecord
from evidence_compass.storage import Storage, sha256

Role = Literal["main", "supplement"]
FetchStatus = Literal["pending", "fetching", "available", "unavailable", "error"]
ParseStatus = Literal["pending", "parsing", "parsed", "partial", "unsupported", "error"]
Acquisition = Literal["fetched", "uploaded"]
Status = Literal["fetching", "ready", "not_found", "needs_review", "error", "not_parsed"]
Action = Literal["retry_fetch", "upload", "confirm_identity"]
WorkState = Literal["fetching", "ready", "no_full_text", "needs_review"]
WorkAction = Literal["upload_supplement"]

FETCH_STATUSES = ("pending", "fetching", "available", "unavailable", "error")
IDENTITY_STATUSES = ("pending", "confirmed", "needs_review", "mismatch")
PARSE_STATUSES = ("pending", "parsing", "parsed", "partial", "unsupported", "error")
FORMATS = ("pdf", "jats", "csv", "other")
MAIN_NAME = "Основной текст"
# Читаются только PDF, JATS XML и CSV; приложения других форматов не загружаются (#44).
READABLE = {".pdf", ".xml", ".nxml", ".csv", ".tsv"}
# Неразобранное приложение этих форматов может содержать таблицы результатов — это пробел;
# изображения, код и служебные формы — нет (shape §3.5).
TABULAR = {".docx", ".doc", ".xlsx", ".xls", ".ods", ".odt", ".rtf", ".zip"}
IMAGES = {".tif", ".tiff", ".gif", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_NOT_READ = "Изображение: значения из него не извлекаются."

LISTED_IN_ARTICLE = "Указано приложением в тексте статьи."
UPLOADED_SUPPLEMENT = "Файл загружен вручную: подтвердите, что это приложение выбранной работы."
CONFIRMED_BY_USER = "Принадлежность файла подтверждена пользователем."
REJECTED_BY_USER = "Пользователь указал, что файл не относится к выбранной работе."
NOT_IN_ARCHIVE = "Приложение указано в статье, но отсутствует в архиве приложений Europe PMC."
NO_PMCID = "Приложение загружается только из Europe PMC, а у работы нет PMCID."
NOTHING_STARTABLE = (
    "Нет работ с готовым основным текстом: дождитесь загрузки, загрузите файл вручную "
    "или подтвердите принадлежность документа."
)
FETCHING = "Документ загружается: дождитесь завершения."
NOT_FETCHABLE = (
    "Приложение загружено вручную: загружать его из источника неоткуда, загрузите новую версию."
)
NOT_READ = "Формат приложения не читается: загрузите вместо него PDF или CSV."


def _known(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class DocumentRecord(Base):
    """Документ публикации; актуальная версия определяется для документа, не для публикации."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(_known("role", ("main", "supplement")), name="role_known"),
        CheckConstraint(_known("fetch_status", FETCH_STATUSES), name="fetch_status_known"),
        UniqueConstraint("publication_id", "source_file"),
        Index(
            "documents_one_main",
            "publication_id",
            unique=True,
            postgresql_where=text("role = 'main'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text)
    # Имя файла приложения в архиве Europe PMC; у основного текста — нет.
    source_file: Mapped[str | None] = mapped_column(Text)
    # Исход последней попытки получения; повтор — новая попытка, версии не удаляются.
    # None — документ не получался: приложение, добавленное пользователем.
    fetch_status: Mapped[str | None] = mapped_column(String(16))
    fetch_detail: Mapped[str | None] = mapped_column(Text)
    # Ссылка на PDF, найденная Unpaywall при неудачной попытке (например, PDF, который PMC
    # отдаёт только в браузере): пользователь открывает её и загружает файл вручную.
    source_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentVersionRecord(Base):
    """Неизменяемая версия документа: файл, способ получения, идентичность и разбор."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "number"),
        CheckConstraint(_known("acquisition", ("fetched", "uploaded")), name="acquisition_known"),
        CheckConstraint(_known("format", FORMATS), name="format_known"),
        CheckConstraint(_known("identity_status", IDENTITY_STATUSES), name="identity_known"),
        CheckConstraint(
            _known("identity_author", ("system", "user")), name="identity_author_known"
        ),
        CheckConstraint(_known("parse_status", PARSE_STATUSES), name="parse_status_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    number: Mapped[int]
    file_name: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int]
    format: Mapped[str] = mapped_column(String(8))
    acquisition: Mapped[str] = mapped_column(String(8))
    # Адрес, откуда получен файл; у ручной загрузки — нет, есть загрузивший пользователь.
    url: Mapped[str | None] = mapped_column(Text)
    account_id: Mapped[int | None] = mapped_column(ForeignKey(Account.id))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    identity_status: Mapped[str] = mapped_column(String(16))
    identity_detail: Mapped[str | None] = mapped_column(Text)
    identity_author: Mapped[str] = mapped_column(String(8), default="system")
    identity_account_id: Mapped[int | None] = mapped_column(ForeignKey(Account.id))
    identity_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parse_status: Mapped[str] = mapped_column(String(16))
    parse_detail: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str | None] = mapped_column(String(64))
    # Разобранное содержимое с адресами: страницы PDF, узлы JATS, параметры чтения CSV.
    parsed: Mapped[dict | None] = mapped_column(JSONB)


@dataclass(frozen=True)
class Row:
    id: int
    name: str
    publication_id: int
    publication_label: str
    role: Role
    version: int | None
    status: Status
    status_detail: str | None
    source_url: str | None
    allowed_actions: list[Action]
    fetch_status: FetchStatus | None
    identity_status: IdentityStatus | None
    parse_status: ParseStatus | None
    sha256: str | None
    acquisition: Acquisition | None


@dataclass(frozen=True)
class WorkDocuments:
    """Строка работы над её документами: состояние до запуска и пробелы (shape §3.5)."""

    publication_id: int
    label: str
    document_count: int
    state: WorkState
    gaps: list[str]
    allowed_actions: list[WorkAction]


@dataclass(frozen=True)
class Extraction:
    documents: list[Row]
    works: list[WorkDocuments]
    startable_publication_ids: list[int]
    start_blocked_reason: str | None


@dataclass(frozen=True)
class Refused:
    message: str


class NotFound(Exception):
    """Документа или работы нет среди выбранных работ текущего исследования."""


# ---------- Чтение ----------


def label(publication: PublicationRecord) -> str:
    author = publication.authors.split(",")[0].split()
    lead = " ".join(filter(None, [author[0] if author else None, str(publication.year or "")]))
    return f"{lead} · {publication.title}" if lead else publication.title


async def _current(db: AsyncSession, document_id: int) -> DocumentVersionRecord | None:
    return await db.scalar(
        select(DocumentVersionRecord)
        .where(DocumentVersionRecord.document_id == document_id)
        .order_by(DocumentVersionRecord.number.desc())
        .limit(1)
    )


def _status(
    document: DocumentRecord, version: DocumentVersionRecord | None
) -> tuple[Status, str | None]:
    if document.fetch_status in ("pending", "fetching"):
        kept = f"Повторная загрузка; версия {version.number} сохраняется." if version else None
        return "fetching", kept
    if version is None and (reason := _unreadable(document.source_file or "")):
        return "not_parsed", reason
    if version is None:
        return ("not_found" if document.fetch_status == "unavailable" else "error"), (
            document.fetch_detail
        )
    status: Status
    if version.parse_status == "error":
        status, detail = "error", version.parse_detail
    elif version.identity_status == "mismatch" and version.identity_author == "user":
        status, detail = "error", version.identity_detail
    elif version.identity_status != "confirmed":
        status, detail = "needs_review", version.identity_detail
    elif version.parse_status == "unsupported" and document.role == "supplement":
        status = "not_parsed"
        detail = (
            _tabular_hint(version.file_name)
            if _tabular(version.file_name)
            else version.parse_detail
        )
    elif version.parse_status in ("unsupported", "partial"):
        # Частичный разбор не прячется за «Готов»; основной текст при этом пригоден к обработке.
        status, detail = "needs_review", version.parse_detail
    else:
        status, detail = "ready", None
    retried = document.fetched_at and document.fetched_at > version.created_at
    if retried and document.fetch_status in ("unavailable", "error"):
        failed = f"Повторная загрузка не удалась, используется версия {version.number}."
        detail = f"{detail} {failed}" if detail else failed
    return status, detail


def _tabular(name: str) -> bool:
    return Path(name).suffix.lower() in TABULAR


def _tabular_hint(name: str) -> str:
    suffix = Path(name).suffix.lstrip(".").upper()
    return (
        f"Формат {suffix} не читается: сохраните таблицу как CSV или PDF и загрузите новой версией."
    )


def _unreadable(name: str) -> str | None:
    """Причина, по которой приложение не загружается: формат известен по имени файла.
    Файл без расширения загружается — формат определит разбор по содержимому."""
    suffix = Path(name).suffix.lower()
    if not suffix or suffix in READABLE:
        return None
    if suffix in IMAGES:
        return IMAGE_NOT_READ
    if suffix in TABULAR:
        return _tabular_hint(name)
    return f"Формат {suffix.lstrip('.').upper()} не читается: поддерживаются PDF, JATS XML и CSV."


def _row(
    document: DocumentRecord,
    version: DocumentVersionRecord | None,
    publication: PublicationRecord,
    busy: bool,
) -> Row:
    status, detail = _status(document, version)
    actions: list[Action] = []
    if not busy and status != "fetching":
        # Приложение, добавленное пользователем, не получалось, и получать его неоткуда;
        # нечитаемый формат не загружается вовсе.
        if document.fetch_status is not None and not _unreadable(document.source_file or ""):
            actions.append("retry_fetch")
        actions.append("upload")
        if version and version.identity_status != "confirmed":
            actions.append("confirm_identity")
    return Row(
        id=document.id,
        name=version.file_name if version else document.name,
        publication_id=publication.id,
        publication_label=label(publication),
        role=document.role,
        version=version.number if version else None,
        status=status,
        status_detail=detail,
        # Ссылка нужна, пока версии нет: после ручной загрузки она уже не действие.
        source_url=document.source_url if version is None else None,
        allowed_actions=actions,
        fetch_status=document.fetch_status,
        identity_status=version.identity_status if version else None,
        parse_status=version.parse_status if version else None,
        sha256=version.sha256 if version else None,
        acquisition=version.acquisition if version else None,
    )


def usable(row: Row) -> bool:
    """Документ можно обрабатывать: подтверждён и разобран хотя бы частично."""
    return (
        row.status != "fetching"
        and row.identity_status == "confirmed"
        and row.parse_status in ("parsed", "partial")
    )


def _singular(n: int) -> bool:
    return n % 10 == 1 and n % 100 != 11


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """Форма слова для числа: 1 приложение, 2 приложения, 5 приложений."""
    if _singular(n):
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


SUPPLEMENTS = ("приложение", "приложения", "приложений")


def _gaps(rows: list[Row]) -> list[str]:
    """Пробелы работы, которые не видны по состоянию основного текста."""
    gaps = []
    main = next((row for row in rows if row.role == "main"), None)
    if main and main.parse_status == "partial" and main.identity_status == "confirmed":
        gaps.append("Основной текст прочитан частично")
    kinds: list[tuple[str, tuple[str, str]]] = []
    for row in rows:
        if row.role != "supplement":
            continue
        if row.status == "not_found":
            kinds.append(("missing", ("не загружено", "не загружены")))
        elif row.status == "error":
            kinds.append(("error", ("с ошибкой", "с ошибкой")))
        elif row.status == "not_parsed" and _tabular(row.name):
            kinds.append(("tabular", ("с таблицами не прочитано", "с таблицами не прочитаны")))
        elif row.status == "needs_review":
            kinds.append(("review", ("требует проверки", "требуют проверки")))
    for kind in ("missing", "tabular", "review", "error"):
        matching = [verbs for name, verbs in kinds if name == kind]
        if not matching:
            continue
        n = len(matching)
        verb = matching[0][0] if _singular(n) else matching[0][1]
        gaps.append(f"{n} {plural(n, SUPPLEMENTS)} {verb}")
    return gaps


def _work_documents(publication: PublicationRecord, rows: list[Row], busy: bool) -> WorkDocuments:
    main = next((row for row in rows if row.role == "main"), None)
    state: WorkState
    if main is None or main.status == "fetching":
        state = "fetching"
    elif main.version is None:
        state = "no_full_text"
    elif usable(main):
        state = "ready"
    else:
        state = "needs_review"
    return WorkDocuments(
        publication_id=publication.id,
        label=label(publication),
        document_count=len(rows),
        state=state,
        gaps=_gaps(rows),
        allowed_actions=[] if busy else ["upload_supplement"],
    )


async def _selected(db: AsyncSession, study_id: int) -> list[PublicationRecord]:
    rows = await db.scalars(
        select(PublicationRecord)
        .where(PublicationRecord.study_id == study_id, PublicationRecord.selected)
        .order_by(PublicationRecord.id)
    )
    return list(rows)


async def _documents(db: AsyncSession, publication_id: int) -> list[DocumentRecord]:
    rows = await db.scalars(
        select(DocumentRecord)
        .where(DocumentRecord.publication_id == publication_id)
        .order_by(DocumentRecord.role, DocumentRecord.id)
    )
    return list(rows)


async def get(db: AsyncSession, account_id: int) -> Extraction | None:
    """Документы выбранных работ; None — исследования ещё нет."""
    study = await questions.find_study(db, account_id)
    if study is None:
        return None
    active = await operations.active(db)
    busy = active is not None
    rows: list[Row] = []
    works: list[WorkDocuments] = []
    for publication in await _selected(db, study.id):
        own = [
            _row(document, await _current(db, document.id), publication, busy)
            for document in await _documents(db, publication.id)
        ]
        rows.extend(own)
        works.append(_work_documents(publication, own, busy))
    startable = [work.publication_id for work in works if work.state == "ready"]
    if active is not None:
        blocked: str | None = operations.busy_message(active)
    else:
        blocked = None if startable else NOTHING_STARTABLE
    return Extraction(rows, works, startable, blocked)


async def _find_work(db: AsyncSession, account_id: int, publication_id: int) -> PublicationRecord:
    """Выбранная работа текущего исследования."""
    study = await questions.find_study(db, account_id)
    publication = await db.get(PublicationRecord, publication_id)
    if study is None or publication is None:
        raise NotFound
    if publication.study_id != study.id or not publication.selected:
        raise NotFound
    return publication


async def _find(db: AsyncSession, account_id: int, document_id: int) -> DocumentRecord:
    document = await db.get(DocumentRecord, document_id)
    if document is None:
        raise NotFound
    await _find_work(db, account_id, document.publication_id)
    return document


@dataclass(frozen=True)
class VersionFile:
    name: str
    format: str
    data: bytes


async def version_file(
    db: AsyncSession, account_id: int, storage: Storage, document_id: int, number: int
) -> VersionFile:
    """Файл версии документа работы исследования: к нему ведут основания справки, в том
    числе прежних ревизий, поэтому выбор работы сейчас не требуется."""
    study = await questions.find_study(db, account_id)
    document = await db.get(DocumentRecord, document_id)
    publication = await db.get(PublicationRecord, document.publication_id) if document else None
    if study is None or publication is None or publication.study_id != study.id:
        raise NotFound
    version = await db.scalar(
        select(DocumentVersionRecord).where(
            DocumentVersionRecord.document_id == document_id,
            DocumentVersionRecord.number == number,
        )
    )
    if version is None:
        raise NotFound
    return VersionFile(version.file_name, version.format, storage.read(version.storage_key))


async def _view(db: AsyncSession, document: DocumentRecord) -> Row:
    publication = await db.get(PublicationRecord, document.publication_id)
    assert publication is not None
    busy = await operations.active(db) is not None
    return _row(document, await _current(db, document.id), publication, busy)


# ---------- Изменения ----------


async def ensure(db: AsyncSession, account_id: int) -> None:
    """Ставит в очередь получение основного текста выбранных работ, у которых его ещё нет.

    Вызывается при переходе к документам и при выборе работы после этого перехода.
    """
    study = await questions.find_study(db, account_id)
    if study is None or study.reached_step not in ("preparation", "result"):
        return
    have = select(DocumentRecord.publication_id).where(DocumentRecord.role == "main")
    missing = await db.scalars(
        select(PublicationRecord.id).where(
            PublicationRecord.study_id == study.id,
            PublicationRecord.selected,
            PublicationRecord.id.not_in(have),
        )
    )
    rows = [
        {
            "publication_id": publication_id,
            "role": "main",
            "name": MAIN_NAME,
            "fetch_status": "pending",
            "created_at": datetime.now(UTC),
        }
        for publication_id in missing
    ]
    if rows:
        # Параллельные выбор и переход не создают второй основной текст.
        await db.execute(
            insert(DocumentRecord)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["publication_id"], index_where=text("role = 'main'")
            )
        )
    await db.commit()


async def _changeable(db: AsyncSession, document: DocumentRecord) -> Busy | Refused | None:
    if busy := await operations.busy(db):
        return busy
    if document.fetch_status in ("pending", "fetching"):
        await db.rollback()
        return Refused(FETCHING)
    return None


async def retry_fetch(db: AsyncSession, account_id: int, document_id: int) -> Row | Busy | Refused:
    """Новая попытка получения; прежние версии сохраняются."""
    document = await _find(db, account_id, document_id)
    await db.refresh(document, with_for_update=True)
    if refused := await _changeable(db, document):
        return refused
    if document.fetch_status is None:
        await db.rollback()
        return Refused(NOT_FETCHABLE)
    if _unreadable(document.source_file or ""):
        await db.rollback()
        return Refused(NOT_READ)
    document.fetch_status, document.fetch_detail = "pending", None
    await db.commit()
    return await _view(db, document)


def _work(publication: PublicationRecord) -> Work:
    return Work(publication.title, publication.doi, publication.pmid, publication.pmcid)


async def _parse_upload(
    name: str, data: bytes, work: Work | None
) -> tuple[Parsed, tuple[IdentityStatus, str]]:
    """Разбор загруженного файла вне транзакции. Основной текст (`work`) сверяется с работой,
    приложение подтверждает пользователь."""
    parsed = await asyncio.to_thread(parse, data, name)
    if work is not None:
        return parsed, identity(parsed, work)
    return parsed, ("needs_review", UPLOADED_SUPPLEMENT)


async def _save_upload(
    db: AsyncSession,
    storage: Storage,
    document: DocumentRecord,
    name: str,
    data: bytes,
    parsed: tuple[Parsed, tuple[IdentityStatus, str]],
    account_id: int,
) -> None:
    key = await asyncio.to_thread(storage.put, document.id, data)
    await _add_version(
        db,
        document,
        name,
        data,
        key,
        parsed[0],
        parsed[1],
        acquisition="uploaded",
        url=None,
        account_id=account_id,
    )


async def upload(
    db: AsyncSession,
    account_id: int,
    storage: Storage,
    document_id: int,
    name: str,
    data: bytes,
) -> Row | Busy | Refused:
    """Ручная загрузка создаёт новую версию документа."""
    document = await _find(db, account_id, document_id)
    if refused := await _changeable(db, document):
        return refused
    publication = await db.get(PublicationRecord, document.publication_id)
    assert publication is not None
    work = _work(publication) if document.role == "main" else None
    # Разбор идёт вне транзакции; слот проверяется снова при фиксации.
    await db.rollback()
    parsed = await _parse_upload(name, data, work)
    await db.refresh(document, with_for_update=True)
    if refused := await _changeable(db, document):
        return refused
    await _save_upload(db, storage, document, name, data, parsed, account_id)
    await db.commit()
    return await _view(db, document)


async def add_supplement(
    db: AsyncSession,
    account_id: int,
    storage: Storage,
    publication_id: int,
    name: str,
    data: bytes,
) -> Row | Busy:
    """Приложение, которого система не нашла: новый документ работы с первой версией."""
    await _find_work(db, account_id, publication_id)
    if busy := await operations.busy(db):
        return busy
    await db.rollback()
    parsed = await _parse_upload(name, data, None)
    if busy := await operations.busy(db):
        return busy
    # Выбор работы мог измениться, пока файл разбирался.
    await _find_work(db, account_id, publication_id)
    document = DocumentRecord(
        publication_id=publication_id,
        role="supplement",
        name=name,
        fetch_status=None,
        created_at=datetime.now(UTC),
    )
    db.add(document)
    await db.flush()
    await _save_upload(db, storage, document, name, data, parsed, account_id)
    await db.commit()
    return await _view(db, document)


async def decide_identity(
    db: AsyncSession, account_id: int, document_id: int, belongs: bool
) -> Row | Busy | Refused:
    """Решение пользователя о принадлежности текущей версии выбранной работе."""
    document = await _find(db, account_id, document_id)
    await db.refresh(document, with_for_update=True)
    if refused := await _changeable(db, document):
        return refused
    version = await _current(db, document.id)
    if version is None:
        await db.rollback()
        return Refused("Версии документа ещё нет: повторите загрузку или загрузите файл вручную.")
    version.identity_status = "confirmed" if belongs else "mismatch"
    version.identity_detail = CONFIRMED_BY_USER if belongs else REJECTED_BY_USER
    version.identity_author, version.identity_account_id = "user", account_id
    version.identity_decided_at = datetime.now(UTC)
    await db.commit()
    return await _view(db, document)


async def _add_version(
    db: AsyncSession,
    document: DocumentRecord,
    name: str,
    data: bytes,
    key: str,
    parsed: Parsed,
    checked: tuple[IdentityStatus, str],
    *,
    acquisition: Acquisition,
    url: str | None,
    account_id: int | None,
) -> DocumentVersionRecord:
    """Вызывается под блокировкой строки документа: номера версий не повторяются."""
    number = await db.scalar(
        select(func.coalesce(func.max(DocumentVersionRecord.number), 0)).where(
            DocumentVersionRecord.document_id == document.id
        )
    )
    version = DocumentVersionRecord(
        document_id=document.id,
        number=(number or 0) + 1,
        file_name=name,
        storage_key=key,
        sha256=sha256(data),
        size=len(data),
        format=parsed.format,
        acquisition=acquisition,
        url=url,
        account_id=account_id,
        created_at=datetime.now(UTC),
        identity_status=checked[0],
        identity_detail=checked[1],
        identity_author="system",
        parse_status=parsed.status,
        parse_detail=parsed.detail,
        parser=parsed.parser,
        parsed=parsed.content,
    )
    db.add(version)
    await db.flush()
    return version


# ---------- Получение (worker) ----------


async def recover(db: AsyncSession) -> None:
    """Получение, прерванное сбоем worker, повторяется; версии не дублируются, т.к. версия
    фиксируется одной транзакцией с исходом попытки."""
    rows = await db.scalars(select(DocumentRecord).where(DocumentRecord.fetch_status == "fetching"))
    for document in rows:
        document.fetch_status = "pending"


async def fetch_next(
    sessionmaker: async_sessionmaker[AsyncSession], retriever: Retriever, storage: Storage
) -> bool:
    """Получает один документ из очереди; False — очередь пуста."""
    async with sessionmaker() as db:
        document = await db.scalar(
            select(DocumentRecord)
            .where(DocumentRecord.fetch_status == "pending")
            .order_by(DocumentRecord.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if document is None:
            return False
        document.fetch_status = "fetching"
        publication = await db.get(PublicationRecord, document.publication_id)
        assert publication is not None
        await db.commit()
        try:
            if document.role == "main":
                await _fetch_main(db, document, publication, retriever, storage)
            else:
                await _fetch_supplements(db, [document], publication, retriever, storage)
        except Exception:
            # Непредвиденная ошибка не оставляет документы работы «в получении» навсегда,
            # включая только что найденные приложения; готовые версии не трогаются.
            await db.rollback()
            stuck = await db.scalars(
                select(DocumentRecord).where(
                    DocumentRecord.publication_id == publication.id,
                    DocumentRecord.fetch_status == "fetching",
                )
            )
            for item in list(stuck):
                await _failed(db, item, NotObtained("Внутренняя ошибка загрузки.", transient=True))
            raise
        return True


async def _failed(db: AsyncSession, document: DocumentRecord, failure: NotObtained) -> None:
    await db.refresh(document, with_for_update=True)
    document.fetch_status = "error" if failure.transient else "unavailable"
    document.fetch_detail = failure.detail
    document.source_url = failure.url
    document.fetched_at = datetime.now(UTC)
    await db.commit()


async def _fetch_main(
    db: AsyncSession,
    document: DocumentRecord,
    publication: PublicationRecord,
    retriever: Retriever,
    storage: Storage,
) -> None:
    try:
        obtained = await retriever.main_text(
            publication.doi, publication.pmcid, publication.open_access
        )
    except NotObtained as failure:
        await _failed(db, document, failure)
        return
    parsed = await asyncio.to_thread(parse, obtained.data, obtained.name)
    checked = identity(parsed, _work(publication))
    key = await asyncio.to_thread(storage.put, document.id, obtained.data)
    await db.refresh(document, with_for_update=True)
    await _add_version(
        db,
        document,
        obtained.name,
        obtained.data,
        key,
        parsed,
        checked,
        acquisition="fetched",
        url=obtained.url,
        account_id=None,
    )
    document.fetch_status, document.fetch_detail = "available", None
    document.source_url = None
    document.fetched_at = datetime.now(UTC)
    # Приложения берутся только из текста, принадлежность которого подтверждена.
    listed = parsed.content.get("supplements", []) if parsed.content else []
    new = []
    if checked[0] == "confirmed" and listed:
        known = set(
            await db.scalars(
                select(DocumentRecord.source_file).where(
                    DocumentRecord.publication_id == publication.id
                )
            )
        )
        for item in listed:
            if item["href"] in known:
                continue
            # Нечитаемый формат сразу получает итог и не входит в запрос архива.
            reason = _unreadable(item["href"])
            now = datetime.now(UTC)
            supplement = DocumentRecord(
                publication_id=publication.id,
                role="supplement",
                name=item["href"],
                source_file=item["href"],
                fetch_status="unavailable" if reason else "fetching",
                fetch_detail=reason,
                fetched_at=now if reason else None,
                created_at=now,
            )
            db.add(supplement)
            if not reason:
                new.append(supplement)
    await db.commit()
    if new:
        # Одним архивом: новые приложения не ждут в очереди по одному.
        await _fetch_supplements(db, new, publication, retriever, storage)


async def _fetch_supplements(
    db: AsyncSession,
    documents: list[DocumentRecord],
    publication: PublicationRecord,
    retriever: Retriever,
    storage: Storage,
) -> None:
    # Приложение нечитаемого формата, поставленное в очередь до #44 или прерванное сбоем,
    # тоже не загружается.
    for document in documents:
        if reason := _unreadable(document.source_file or ""):
            await _failed(db, document, NotObtained(reason, transient=False))
    documents = [document for document in documents if not _unreadable(document.source_file or "")]
    if not documents:
        return
    pmcid = publication.pmcid
    if not pmcid:
        for document in documents:
            await _failed(db, document, NotObtained(NO_PMCID, transient=False))
        return
    names = [document.source_file for document in documents if document.source_file]
    try:
        files = await retriever.supplements(pmcid, names)
    except NotObtained as failure:
        for document in documents:
            await _failed(db, document, failure)
        return
    for document in documents:
        name = document.source_file or ""
        if name not in files:
            await _failed(db, document, NotObtained(NOT_IN_ARCHIVE, transient=False))
            continue
        data = files[name]
        if data is None:
            detail = f"Приложение больше {retriever.limit // 2**20} МБ и не загружено."
            await _failed(db, document, NotObtained(detail, transient=False))
            continue
        parsed = await asyncio.to_thread(parse, data, document.name)
        key = await asyncio.to_thread(storage.put, document.id, data)
        await db.refresh(document, with_for_update=True)
        await _add_version(
            db,
            document,
            document.name,
            data,
            key,
            parsed,
            ("confirmed", LISTED_IN_ARTICLE),
            acquisition="fetched",
            url=supplements_url(pmcid),
            account_id=None,
        )
        document.fetch_status, document.fetch_detail = "available", None
        document.fetched_at = datetime.now(UTC)
        await db.commit()
