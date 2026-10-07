"""Подменённая обработка работы: контролируемые итоги вместо модели (#14; сама обработка — #15)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from evidence_compass.extraction import WorkInput, WorkResult

EXTRACTED = WorkResult("measures_extracted", 2, None)


class Crash(BaseException):
    """Падение процесса worker: не перехватывается как ошибка работы."""


@dataclass
class FakeExtractor:
    """Отвечает `EXTRACTED`, пока тест не задал другой итог или исключение.

    `replies` расходуются по одному на работу; `during` выполняется один раз внутри обработки
    работы — так тест подаёт команду, пока worker занят работой.
    """

    replies: list[WorkResult | BaseException] = field(default_factory=list)
    calls: list[int] = field(default_factory=list)
    during: Callable[[], Awaitable[object]] | None = None

    async def extract(self, work: WorkInput) -> WorkResult:
        self.calls.append(work.publication_id)
        if self.during is not None:
            hook, self.during = self.during, None
            await hook()
        reply = self.replies.pop(0) if self.replies else EXTRACTED
        if isinstance(reply, BaseException):
            raise reply
        return reply
