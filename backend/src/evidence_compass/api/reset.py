"""Временная очистка данных для отладки: удаляется вместе с кнопкой «Очистить данные».

Удалить: этот файл и `tests/test_reset.py`, строку подключения в `api/__init__.py`,
`frontend/src/app/ClearDataButton.tsx` и её строки в `AppShell.tsx`, затем `pnpm gen:api`.
"""

import shutil

from fastapi import APIRouter, Request, status
from sqlalchemy import text

from evidence_compass.api.auth import Db
from evidence_compass.db import Base

router = APIRouter()

# Учётные записи и вход переживают очистку.
KEPT_TABLES = {"accounts", "sessions"}


@router.delete("/study", operation_id="clearStudyData", status_code=status.HTTP_204_NO_CONTENT)
async def clear_study_data(request: Request, db: Db) -> None:
    """Удаляет все данные исследований и файлы документов."""
    tables = ", ".join(
        table.name for table in Base.metadata.sorted_tables if table.name not in KEPT_TABLES
    )
    await db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await db.commit()
    shutil.rmtree(request.app.state.storage.root, ignore_errors=True)
