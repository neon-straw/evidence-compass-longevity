"""Долговечные операции и общий слот установки (ADR 0003, 0008).

Источник истины операции — строка в PostgreSQL. Незавершённой может быть только одна
операция на установку: это гарантирует частичный уникальный индекс, а не код.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass.db import Base

# Словарь выполнения операции (spec, «Отбор и состояния»).
STATUSES = (
    "queued",
    "running",
    "pause_requested",
    "paused",
    "waiting_llm",
    "terminate_requested",
    "completed",
    "failed",
    "terminated",
)
# Незавершённые состояния занимают общий слот, включая паузу и ожидание LLM.
ACTIVE = STATUSES[:6]
KINDS = {
    "criteria_preparation": "подготовка критериев",
    "search": "поиск",
    "extraction": "извлечение",
}

_in = ", ".join(f"'{value}'" for value in ACTIVE)


class OperationRecord(Base):
    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{value}'" for value in STATUSES) + ")",
            name="status_known",
        ),
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{value}'" for value in KINDS) + ")", name="kind_known"
        ),
        Index(
            "operations_one_active",
            text("(true)"),
            unique=True,
            postgresql_where=text(f"status IN ({_in})"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    # Неизменяемый снимок входов: worker продолжает по нему после сбоя.
    input: Mapped[dict] = mapped_column(JSONB)
    # Ключ идемпотентности команды запуска: повтор с тем же ключом возвращает эту операцию.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    # Модель из настроек при запуске; после ответа — фактические модель и провайдер.
    model: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True)
class Busy:
    """Общий слот занят: изменяющая команда отклонена, сохранённое можно читать."""

    message: str


async def active(db: AsyncSession) -> OperationRecord | None:
    return await db.scalar(select(OperationRecord).where(OperationRecord.status.in_(ACTIVE)))


def busy_message(operation: OperationRecord) -> str:
    return (
        f"Идёт {KINDS[operation.kind]}: изменения станут доступны после её завершения. "
        "Сохранённые данные можно просматривать."
    )


async def busy(db: AsyncSession) -> Busy | None:
    """Занят ли слот; при занятом слоте незавершённая транзакция откатывается."""
    operation = await active(db)
    if operation is None:
        return None
    result = Busy(busy_message(operation))
    await db.rollback()
    return result
