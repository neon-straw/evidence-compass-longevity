"""Извлечение карточки и показателей работы моделью (#15; spec п. 10–15, AC07–08).

Один вызов модели на работу по всем пригодным документам снимка. Модель получает текст с
разметкой мест (страницы PDF, абзацы и таблицы JATS) и ключи документов; ответ проверяется
схемой `cards.Card` и сверкой ссылок, затем основания привязываются к адресам в документах
(`evidence`). Малая таблица CSV передаётся по записям; у большого исходного массива модель
видит только заголовки, поэтому его записи не превращаются в строки показателей.
"""

from dataclasses import dataclass

from evidence_compass import cards, evidence
from evidence_compass.documents import plural
from evidence_compass.extraction import ModelUnavailable, SourceDocument, WorkInput, WorkResult
from evidence_compass.llm import LLM, Completion, LLMInvalidResponse, LLMUnavailable

NO_FULL_TEXT = "В документах работы нет полного текста: извлекать показатели не из чего."

SYSTEM_PROMPT = """\
Ты извлекаешь из научной публикации опубликованные показатели продолжительности жизни и \
возраста животных и составляешь карточку статьи. Ты не оцениваешь качество исследования и \
не делаешь собственных методологических замечаний.

Что является показателем:
- Продолжительность жизни или возраст животных объектов исследования, которые сообщает \
публикация: возраст смерти, среднее, медиана или максимум продолжительности жизни, возраст \
особи, модельная оценка возраста реального животного, интервал наблюдения живой особи, \
возраст созревания.
- Не показатели: возраст подопытных животных на момент эксперимента или забора образцов, \
границы возрастных групп («молодые 2–4 года, старые 20 лет»), длительность эксперимента, \
доли выживших и риски, индексы и коэффициенты без единиц времени.

Правила показателей:
- Одна строка measures — одно значение одной особи или группы в одних условиях. Не \
объединяй группы, пол, условия (природа/неволя), эксперименты или анализы в одну строку.
- measure_kind передаёт точный смысл источника: среднее (mean), медиана (median), \
наблюдаемый максимум (observed_maximum), возраст отдельной особи (individual_age), модельная \
оценка возраста реального животного (modelled_age), интервал наблюдения/выживания \
(survival_interval), возраст созревания (maturity_age), иное (other). Они не \
взаимозаменяемы: «maximum lifespan» — observed_maximum, не mean; если в определении автора \
одновременно среднее и максимум, опиши это в unresolved.
- Максимум группы не предел вида; длительность наблюдения не возраст от рождения; диапазон \
значений не доверительный интервал. Неизвестное — null, не ноль и не догадка.
- Значения дословно на языке источника, без перевода: value_original — только число или \
числа как в тексте, без единиц и знаков сравнения; unit_original — единицы как в тексте \
(years, days); знак или слово оговорки (>, ≥, <, about, at least) — в qualifier словами \
источника. Не пересчитывай единицы. Например, «>30 years» — value_original «30», \
unit_original «years», qualifier «more than».
- definition — определение словами авторов на языке источника.
- species_name_in_source — название как в источнике, даже историческое; species_label — \
объект исследования из списка запроса. Таксономическую оговорку сохрани в taxonomy_note.
- origin_kind own_result — только собственный результат этой работы: методы, результаты, \
таблицы. Значение из введения или обсуждения со ссылкой на другую работу или «it is \
reported» — secondary_report, даже если ссылка не указана явно рядом.
- Показатели других видов не выдавай за объекты исследования: перечисли их в set_aside.
- Симуляции, записи исходных массивов данных и значения, считываемые только с графиков, \
не становятся показателями: перечисли их в set_aside. Не вычисляй новых значений.
- Если источник даёт разные значения одного результата или группу/условия значения нельзя \
установить однозначно, опиши это в unresolved, не выбирай одно значение молча.
- Если показателей нет, верни пустой measures: это допустимый итог, не придумывай строки. \
Строка без числа в value_original («сведений нет», null) — не показатель.

Основания: каждое значение, утверждение, вывод и ограничение авторов подкрепи дословной \
цитатой (evidence) с ключом документа и местом, как оно размечено в запросе: «страница N», \
«§N», для таблицы — её название, «строка N, столбец <заголовок>», для CSV — «запись N, \
столбец <заголовок>». Для значения из текста цитата включает число и его единицы. Методы и \
условия присоединяй к показателю (context_evidence) только при явной связи с его группой. \
author_limitations — только ограничения, прямо названные авторами. Текст карточки \
(subjects, methods, results, conclusions) пиши по-русски, цитаты — дословно. Ключи M1, L1 \
и т. п. локальны для ответа; идентификаторы и статусы задаёт сервер."""


class SchemaFailed(Exception):
    """Модель отвечала, но ни один ответ в пределах бюджета не прошёл проверку схемы."""


@dataclass(frozen=True)
class Prompt:
    user: str
    documents: dict[str, int]
    objects: dict[str, str]


def _object_label(item: dict) -> str:
    name, scientific = item.get("name") or "", item.get("scientific_name") or ""
    if name and scientific and name != scientific:
        return f"{name} ({scientific})"
    return name or scientific


def _objects(objects: list[dict]) -> dict[str, str]:
    """Любое название объекта в нижнем регистре → его подпись."""
    names: dict[str, str] = {}
    for item in objects:
        label = _object_label(item)
        for name in [
            label,
            item.get("name"),
            item.get("scientific_name"),
            *item.get("aliases", []),
        ]:
            if name:
                names[name.strip().casefold()] = label
    return names


def _pdf(content: dict) -> list[str]:
    return [
        f"[страница {number}]\n{text}"
        for number, text in enumerate(content.get("pages", []), 1)
        if text.strip()
    ]


def _jats(content: dict) -> list[str]:
    parts = [
        f"[§{number}] {block['text']}" for number, block in enumerate(content.get("blocks", []), 1)
    ]
    for table in content.get("tables", []):
        title = " ".join(filter(None, [table.get("label"), table.get("caption")])) or "Таблица"
        rows = [
            f"  строка {number}: " + " | ".join(cells)
            for number, cells in enumerate(table.get("rows", []), 1)
        ]
        parts.append(f"[{title}]\n" + "\n".join(rows))
    return parts


def _csv(content: dict) -> list[str]:
    header = " | ".join(content.get("header", []))
    if "rows" in content:
        # Многострочная ячейка остаётся в своей записи: адрес — номер записи, не строки файла.
        records = [
            f"[запись {number}] " + " | ".join(" ".join(cell.split()) for cell in cells)
            for number, cells in enumerate(content["rows"], 1)
        ]
        return ["\n".join([f"[заголовки] {header}", *records])]
    return [
        f"[заголовки] {header}\nЗаписей: {content.get('records', 0)}. Исходный массив данных: "
        "записи в запрос не переданы и не превращаются в показатели."
    ]


def _parts(document: SourceDocument) -> list[str]:
    content = document.content or {}
    match document.format:
        case "pdf":
            return _pdf(content)
        case "jats":
            return _jats(content)
        case "csv":
            return _csv(content)
    return []


def _full_text(document: SourceDocument) -> bool:
    """Полный текст статьи: страницы PDF с текстом или абзацы JATS, не только метаданные.

    Приложение не заменяет основной текст.
    """
    content = document.content or {}
    if document.role != "main":
        return False
    if document.format == "pdf":
        return any(page.strip() for page in content.get("pages", []))
    return document.format == "jats" and bool(content.get("blocks"))


def prompt(work: WorkInput) -> Prompt | None:
    """Запрос модели; None — в документах работы нет полного текста."""
    if not any(_full_text(document) for document in work.documents):
        return None
    ordered = sorted(work.documents, key=lambda document: document.role != "main")
    keys = {f"D{number}": document for number, document in enumerate(ordered, 1)}
    objects = [_object_label(item) for item in work.objects]
    sections = [
        f"Исследовательский вопрос: {work.question}",
        "Объекты исследования (species_label): " + ("; ".join(objects) or "не заданы"),
        f"Публикация: {work.title}",
    ]
    for key, document in keys.items():
        role = "основной текст" if document.role == "main" else "приложение"
        head = f"=== {key}: {document.name} — {role}, {document.format.upper()} ==="
        sections.append("\n".join([head, *_parts(document)]))
    return Prompt(
        user="\n\n".join(sections),
        documents={key: document.version_id for key, document in keys.items()},
        objects=_objects(work.objects),
    )


def result(candidate: cards.Candidate, completion: Completion) -> WorkResult:
    """Итог работы по проверенной карточке: пробелы и отложенное не прячутся за числом строк."""
    unresolved = candidate.content["unresolved"]
    set_aside = candidate.content["set_aside"]
    notes = []
    if unresolved:
        first = unresolved[0]["description"].rstrip(".")
        notes.append(f"Требуют решения: {len(unresolved)} ({first}).")
    if set_aside:
        notes.append(
            f"Не стали показателями: {len(set_aside)} "
            f"{plural(len(set_aside), ('запись', 'записи', 'записей'))} "
            "(симуляции, исходные данные, другие виды и т. п.)."
        )
    count = len(candidate.measures)
    if unresolved:
        outcome = "unresolved"
    elif count:
        outcome = "measures_extracted"
    else:
        outcome = "no_measures"
    return WorkResult(
        outcome,
        count,
        " ".join(notes) or None,
        card=candidate,
        model=completion.model,
        provider=completion.provider,
    )


class ModelExtractor:
    """Шов `extraction.Extractor` на модели с ограниченным бюджетом попыток (ADR 0007)."""

    def __init__(self, llm: LLM, attempts: int) -> None:
        self._llm = llm
        self._attempts = attempts

    async def extract(self, work: WorkInput) -> WorkResult:
        request = prompt(work)
        if request is None:
            return WorkResult("unavailable", None, NO_FULL_TEXT)
        try:
            completion = await self._complete(request.user)
        except SchemaFailed as failed:
            return WorkResult("error", None, f"Ответ модели не прошёл проверку схемы: {failed}")
        try:
            candidate = cards.check(completion.value, request.documents, request.objects)
        except cards.Rejected as rejected:
            return WorkResult("error", None, f"Ответ модели отклонён: {rejected}.")
        documents = {document.version_id: document for document in work.documents}
        return result(evidence.verify(candidate, documents), completion)

    async def _complete(self, user: str) -> Completion[cards.Card]:
        """Модель не меняется между попытками. Исчерпанный бюджет — ожидание продолжения,
        если модель не отвечала, и ошибка работы, если последний ответ был вне схемы."""
        failure: LLMUnavailable | None = None
        for _ in range(self._attempts):
            try:
                return await self._llm.complete(system=SYSTEM_PROMPT, user=user, schema=cards.Card)
            except LLMUnavailable as error:
                failure = error
        reason = f"{failure}; попыток: {self._attempts}."
        if isinstance(failure, LLMInvalidResponse):
            raise SchemaFailed(reason)
        raise ModelUnavailable(reason)
