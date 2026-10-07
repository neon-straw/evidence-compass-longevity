"""Черновик исследовательского вопроса и неизменяемые версии его формулировки."""

from dataclasses import dataclass
from datetime import UTC, datetime

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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass.accounts import Account
from evidence_compass.db import Base

BLANK_QUESTION = "Сначала сформулируйте вопрос."


class StudyRecord(Base):
    """Текущий исследовательский вопрос аккаунта.

    В первой версии у аккаунта один вопрос: `account_id` уникален. Несколько вопросов
    потребуют снять это ограничение; версии останутся привязаны к строке исследования.
    """

    __tablename__ = "studies"
    __table_args__ = (
        CheckConstraint(
            "reached_step IN ('question', 'criteria', 'search', 'selection', "
            "'preparation', 'result')",
            name="reached_step_known",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey(Account.id, ondelete="CASCADE"), unique=True)
    reached_step: Mapped[str] = mapped_column(String(32), default="question")
    draft_text: Mapped[str] = mapped_column(Text, default="")
    draft_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuestionVersionRecord(Base):
    """Снимок формулировки в момент запуска подготовки критериев. Строка не обновляется."""

    __tablename__ = "question_versions"
    __table_args__ = (UniqueConstraint("study_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    number: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class Draft:
    text: str
    saved_at: datetime | None


@dataclass(frozen=True)
class Version:
    version: int
    text: str
    created_at: datetime


@dataclass(frozen=True)
class NotReady:
    message: str


def _draft(study: StudyRecord | None) -> Draft:
    if study is None or study.draft_saved_at is None:
        return Draft("", None)
    return Draft(study.draft_text, study.draft_saved_at)


async def find_study(
    db: AsyncSession, account_id: int, *, lock: bool = False
) -> StudyRecord | None:
    statement = select(StudyRecord).where(StudyRecord.account_id == account_id)
    if lock:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def reached_step(db: AsyncSession, account_id: int) -> str:
    study = await find_study(db, account_id)
    return study.reached_step if study else "question"


async def get_draft(db: AsyncSession, account_id: int) -> Draft:
    return _draft(await find_study(db, account_id))


async def save_draft(db: AsyncSession, account_id: int, text: str) -> Draft:
    study = await find_study(db, account_id)
    now = datetime.now(UTC)
    if study is None:
        study = StudyRecord(account_id=account_id, draft_text=text, draft_saved_at=now)
        db.add(study)
    else:
        study.draft_text = text
        study.draft_saved_at = now
    await db.commit()
    return Draft(study.draft_text, study.draft_saved_at)


async def list_versions(db: AsyncSession, account_id: int) -> list[Version]:
    study = await find_study(db, account_id)
    if study is None:
        return []
    rows = await db.scalars(
        select(QuestionVersionRecord)
        .where(QuestionVersionRecord.study_id == study.id)
        .order_by(QuestionVersionRecord.number)
    )
    return [Version(row.number, row.text, row.created_at) for row in rows]


async def latest_version(db: AsyncSession, study_id: int) -> QuestionVersionRecord | None:
    return await db.scalar(
        select(QuestionVersionRecord)
        .where(QuestionVersionRecord.study_id == study_id)
        .order_by(QuestionVersionRecord.number.desc())
        .limit(1)
    )


async def freeze(
    db: AsyncSession, account_id: int
) -> tuple[StudyRecord, QuestionVersionRecord] | NotReady:
    """Фиксирует новую версию формулировки и открывает шаг критериев, не завершая транзакцию.

    Любой непустой вопрос сохраняется как есть. Пустой черновик версию не создаёт.
    Текст версии — черновик на момент запуска; поздняя правка его не меняет.
    Строка исследования остаётся заблокированной до commit вызывающего.
    """
    study = await find_study(db, account_id, lock=True)
    text = study.draft_text if study else ""
    if text.strip() == "":
        return NotReady(BLANK_QUESTION)
    assert study is not None
    number = await db.scalar(
        select(func.max(QuestionVersionRecord.number)).where(
            QuestionVersionRecord.study_id == study.id
        )
    )
    version = QuestionVersionRecord(
        study_id=study.id, number=(number or 0) + 1, text=text, created_at=datetime.now(UTC)
    )
    db.add(version)
    # Подготовка — единственный переход, который открывает критерии. Дальше шаг не откатывается.
    if study.reached_step == "question":
        study.reached_step = "criteria"
    await db.flush()
    return study, version
