"""Файловое хранилище версий документов (ADR 0004, 0008).

Файл принадлежит конкретному документу и не меняется: путь — id документа и SHA-256
содержимого. БД и файловая система не образуют общую транзакцию, поэтому файл пишется
через временный файл и атомарное переименование до фиксации метаданных: сбой оставляет
в худшем случае несвязанный файл, но не запись о несуществующем.
"""

import hashlib
import os
import tempfile
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Storage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, document_id: int, data: bytes) -> str:
        """Сохраняет содержимое и возвращает ключ; тот же файл документа не пишется дважды."""
        key = f"{document_id}/{sha256(data)}"
        path = self._path(key)
        if path.exists():
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return key

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()
