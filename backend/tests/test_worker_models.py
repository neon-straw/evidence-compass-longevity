"""Worker — отдельный процесс (ADR 0008): его импорты должны регистрировать все таблицы,
на которые ссылаются внешние ключи. В тестах API всё уже импортировано, поэтому проверка
идёт в чистом интерпретаторе."""

import subprocess
import sys

CHECK = """
import evidence_compass.worker
from evidence_compass.db import Base
tables = Base.metadata.tables
print(sorted(
    f"{table.name}.{key.parent.name}"
    for table in tables.values()
    for key in table.foreign_keys
    if key.target_fullname.split(".")[0] not in tables
))
"""


def test_worker_process_knows_every_foreign_key_target():
    result = subprocess.run(
        [sys.executable, "-c", CHECK], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"
