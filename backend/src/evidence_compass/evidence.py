"""Адресуемое основание: где в версии документа стоит цитата модели (#16; spec «Адресуемое
основание», AC09).

Модель указывает место своей подписью (`location`); адрес задаёт сервер по разобранному
содержимому: физическая страница PDF, путь и ID узла JATS, строка и ячейка таблицы JATS,
запись и столбец CSV. Совпадение текста подтверждает только наличие цитаты; для значения
показателя отдельно сверяются число (целиком, не подстрокой), единицы сразу за ним, строка и
столбец таблицы. Неуверенная привязка — `needs_review` с диагностикой; адрес не выдумывается:
он пуст, если цитата не найдена или встречается в нескольких местах.

Не сверяются: субъект, метод и определение показателя; у утверждений карточки и оснований
контекста `verified` означает только найденную цитату, не её относимость к группе.
"""

import itertools
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evidence_compass.cards import Candidate, VerificationStatus
    from evidence_compass.extraction import SourceDocument

NOT_PARSED = "Содержимое документа не разобрано."
NOT_FOUND = "Цитата не найдена в документе."
NO_UNIT = "Единицы значения не указаны."

# ---------- Нормализация ----------

_QUOTES = set("\"'`‘’‚‛“”„‟«»′″")
_WORD_HYPHEN = re.compile(r"(?<=[^\W\d_])[-‐‑]\s*(?=[^\W\d_])")
_DIGIT_SPACE = re.compile(r"(?<=\d)\s+(?=\d)")
_DASHES = str.maketrans({char: "-" for char in "‐‑‒–—−"})


def _key(text: str) -> str:
    """Форма для поиска цитаты: регистр, лигатуры, переносы слов и разрывы строк PDF,
    кавычки и тире не мешают совпадению. Пробел между числами сохраняется, чтобы соседние
    числа не слились в одно."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("\u00ad", "")
    text = _WORD_HYPHEN.sub("", text).translate(_DASHES)
    text = _DIGIT_SPACE.sub("\x00", text)
    return "".join(char for char in text if char not in _QUOTES and not char.isspace())


def _alnum(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text).casefold() if char.isalnum()
    )


# Число целиком: 41 не совпадает с 141, 4.1 или 41.5.
_NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*")


def _numbers(text: str) -> list[str]:
    return [number.replace(",", ".") for number in _NUMBER.findall(text)]


def _occurrences(needle: str, haystack: str) -> int:
    """Вхождения цитаты, не разрезающие число на краях."""
    count, start = 0, haystack.find(needle)
    while start >= 0:
        end = start + len(needle)
        before = haystack[start - 1] if start else ""
        after = haystack[end : end + 2]
        cut_before = needle[0].isdigit() and (before.isdigit() or before in ".,")
        cut_after = needle[-1].isdigit() and bool(re.match(r"\d|[.,]\d", after))
        if not (cut_before or cut_after):
            count += 1
        start = haystack.find(needle, start + 1)
    return count


# ---------- Места документа ----------


@dataclass(frozen=True)
class Span:
    """Место поиска цитаты: страница, узел, строка таблицы или запись CSV."""

    locator: dict
    text: str
    place: str
    where: str
    page: int | None = None
    block: int | None = None
    table: str | None = None
    row: int | None = None
    record: int | None = None
    cells: list[str] | None = None
    headers: list[str] | None = None
    # Подпись таблицы: единицы часто указаны в ней, а не в ячейке.
    caption: str = ""
    continued: int | None = None


def _spans(document: "SourceDocument") -> list[Span]:
    content = document.content or {}
    match document.format:
        case "pdf":
            return [
                Span({"kind": "pdf", "page": n}, text, f"страница {n}", f"на странице {n}", page=n)
                for n, text in enumerate(content.get("pages", []), 1)
            ]
        case "jats":
            return _jats_spans(content)
        case "csv":
            return _csv_spans(content)
    return []


def _jats_spans(content: dict) -> list[Span]:
    spans = [
        Span(
            {"kind": "jats", "path": block["path"], "id": block.get("id")},
            block["text"],
            f"§{n}",
            f"в §{n}",
            block=n,
        )
        for n, block in enumerate(content.get("blocks", []), 1)
    ]
    for table in content.get("tables", []):
        title = " ".join(filter(None, [table.get("label"), table.get("caption")])) or "Таблица"
        rows = table.get("rows", [])
        for n, cells in enumerate(rows, 1):
            locator = {
                "kind": "jats_row",
                "path": table["path"],
                "id": table.get("id"),
                "label": table.get("label"),
                "row": n,
            }
            spans.append(
                Span(
                    locator,
                    " ".join(cells),
                    f"{title} строка {n}",
                    f"в строке {n} таблицы «{title}»",
                    table=_alnum(table.get("label") or title),
                    row=n,
                    cells=cells,
                    headers=rows[0] if n > 1 else [],
                    caption=title,
                )
            )
    return spans


def _csv_spans(content: dict) -> list[Span]:
    # У большого исходного массива записей в разобранном содержимом нет: адреса тоже нет.
    base = {"dialect": content.get("dialect"), "encoding": content.get("encoding")}
    return [
        Span(
            {"kind": "csv", "record": n, **base},
            " ".join(cells),
            f"запись {n}",
            f"в записи {n}",
            record=n,
            cells=cells,
            headers=content.get("header", []),
        )
        for n, cells in enumerate(content.get("rows", []), 1)
    ]


# ---------- Подпись места моделью ----------


@dataclass(frozen=True)
class Hint:
    page: int | None = None
    block: int | None = None
    table: str | None = None
    row: int | None = None
    record: int | None = None
    column: str | None = None


def _int(pattern: str, text: str) -> int | None:
    found = re.search(pattern, text, re.IGNORECASE)
    return int(found.group(1)) if found else None


def hint(location: str) -> Hint:
    """Разбор подписи места по разметке запроса: `страница N`, `§N`,
    `<таблица> строка N, столбец X`, `запись N, столбец X`."""
    row = re.search(r"(.*?)\bстрок\w*\s*(\d+)", location, re.IGNORECASE)
    column = re.search(r"\b(?:столб\w*|column)\s*(.+?)\s*$", location, re.IGNORECASE)
    return Hint(
        page=_int(r"(?:страниц\w*|стр\.|page)\s*(\d+)", location),
        block=_int(r"§\s*(\d+)", location),
        table=(_alnum(row.group(1)) or None) if row else None,
        row=int(row.group(2)) if row else None,
        record=_int(r"\bзапис\w*\s*(\d+)", location),
        column=column.group(1).strip(" «»\"'") if column else None,
    )


def _applies(place: Hint, span: Span) -> bool | None:
    """Совпадает ли подпись модели с местом; None — подпись не про такие места."""
    if span.page is not None:
        return None if place.page is None else place.page == span.page
    if span.block is not None:
        return None if place.block is None else place.block == span.block
    if span.record is not None:
        return None if place.record is None else place.record == span.record
    if place.row is None:
        return None
    # Ярлык таблицы («Table 1») входит в подпись модели, подпись может быть длиннее.
    same_table = place.table is None or bool(span.table and span.table in place.table)
    return same_table and place.row == span.row


# ---------- Привязка цитаты ----------


def _result(
    item: dict, span: Span | None, status: "VerificationStatus", diagnostic: str | None
) -> dict:
    return {
        **item,
        "locator": span.locator if span else None,
        "verification_status": status,
        "diagnostic": diagnostic,
    }


def _found(needle: str, spans: list[Span]) -> list[tuple[Span, int]]:
    found = [(span, n) for span in spans if (n := _occurrences(needle, _key(span.text)))]
    if found or not spans or spans[0].page is None:
        return found
    # Цитата через разрыв страниц: адрес — страница её начала.
    pairs = []
    for first, second in itertools.pairwise(spans):
        joined = _key(f"{first.text}\n{second.text}")
        if n := _occurrences(needle, joined):
            pairs.append((replace(first, continued=second.page), n))
    return pairs


def _places(found: list[tuple[Span, int]]) -> str:
    if all(span.page is not None for span, _ in found):
        return "страницы " + ", ".join(str(span.page) for span, _ in found)
    return ", ".join(span.place for span, _ in found)


def _locate(item: dict, document: "SourceDocument | None") -> tuple[dict, Span | None]:
    if document is None or document.content is None:
        return _result(item, None, "needs_review", NOT_PARSED), None
    # Точка или запятая на краю цитаты — не часть утверждения.
    needle = _key(item["quote"]).strip(".,;:")
    found = _found(needle, _spans(document)) if needle else []
    if not found:
        return _result(item, None, "needs_review", NOT_FOUND), None
    place = hint(item["location"])
    if sum(n for _, n in found) == 1:
        [(span, _)] = found
    else:
        named = [(span, n) for span, n in found if _applies(place, span)]
        if len(named) != 1 or named[0][1] != 1:
            diagnostic = (
                f"Цитата встречается в документе несколько раз ({_places(found)}): "
                "место не установлено."
            )
            return _result(item, None, "needs_review", diagnostic), None
        [(span, _)] = named
    notes = []
    if _applies(place, span) is False:
        notes.append(f"Модель указала «{item['location']}», цитата найдена {span.where}.")
    if span.continued:
        notes.append(f"Цитата продолжается на странице {span.continued}.")
    return _result(item, span, "verified", " ".join(notes) or None), span


def locate(item: dict, document: "SourceDocument | None") -> dict:
    """Основание с адресом в документе и статусом привязки."""
    return _locate(item, document)[0]


# ---------- Значение показателя ----------

_UNITS = {
    "year": r"years?|yrs?|y|лет|года?|год",
    "month": r"months?|mos?|месяц\w*|мес",
    "week": r"weeks?|wks?|недел\w*",
    "day": r"days?|d|дн\w*|сут\w*|дней",
}
_JOINERS = {"±", "+/-", "-", "to", "and", "or", ",", "до", "и"}


def _unit_pattern(span: str) -> re.Pattern:
    words = next(
        (words for words in _UNITS.values() if re.search(rf"\b(?:{words})\b", span, re.I)),
        re.escape(span.strip()),
    )
    # Единица — отдельное слово, не часть скорости вида yr−1.
    return re.compile(rf"(?<![^\W\d_])(?:{words})(?![^\W\d_])(?!\s*-\s*1\b)", re.IGNORECASE)


def _follows(text: str, number: str, pattern: re.Pattern) -> bool:
    """Единица стоит сразу за числом; связанные с ним числа (± 12, – 15, to 15) и оговорка
    «+» (40+ years) между ними допустимы. «per year», «/yr» и единица дальше по фразе
    значение не подтверждают."""
    for match in _NUMBER.finditer(text):
        if match.group().replace(",", ".") != number:
            continue
        pos = match.end()
        while True:
            following = _NUMBER.search(text, pos)
            if (
                following is None
                or text[pos : following.start()].strip().casefold() not in _JOINERS
            ):
                break
            pos = following.end()
        if pattern.match(text[pos:].lstrip(" (-+")):
            return True
    return False


def _text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


def _check_text(item: dict, row: dict) -> str | None:
    value, unit_name = row["content"]["value_original"], row["content"]["unit_original"]
    quote = _text(item["quote"])
    numbers = _numbers(_text(value))
    if any(number not in _numbers(quote) for number in numbers):
        return f"В цитате нет значения {value}."
    if not unit_name.strip():
        return NO_UNIT
    if not _follows(quote, numbers[0], _unit_pattern(_text(unit_name))):
        return f"Сразу за значением {value} в цитате нет единиц «{unit_name}»."
    return None


def _column(place: Hint, span: Span, columns: list[int], value: str) -> int | str:
    """Номер столбца значения (с 0) или причина, почему он не установлен."""
    headers = span.headers or []
    if place.column is None:
        numeric = [cell for cell in span.cells or [] if _numbers(cell)]
        if len(numeric) == 1 and len(columns) == 1:
            return columns[0]
        return f"Не указан столбец значения {value}: в строке несколько чисел."
    if place.column.isdigit():
        named = [int(place.column) - 1]
    else:
        named = [n for n, header in enumerate(headers) if _alnum(header) == _alnum(place.column)]
    if not named:
        return f"Столбец «{place.column}» не найден."
    if len(named) > 1:
        return f"Столбец «{place.column}» неоднозначен: одинаковые заголовки."
    if named[0] not in columns:
        return f"В столбце «{place.column}» нет значения {value}."
    return named[0]


def _check_cell(item: dict, span: Span, row: dict) -> tuple[str | None, dict]:
    value, unit_name = row["content"]["value_original"], row["content"]["unit_original"]
    place = hint(item["location"])
    if _applies(place, span) is False:
        return f"Модель указала «{item['location']}», значение найдено {span.where}.", span.locator
    numbers = _numbers(_text(value))
    cells = span.cells or []
    columns = [
        n for n, cell in enumerate(cells) if all(x in _numbers(_text(cell)) for x in numbers)
    ]
    if not columns:
        return f"В {span.place} нет значения {value}.", span.locator
    column = _column(place, span, columns, value)
    if isinstance(column, str):
        return column, span.locator
    header = (span.headers or [])[column] if column < len(span.headers or []) else ""
    if span.locator["kind"] == "csv":
        locator = {
            **span.locator,
            "column": column + 1,
            "header": header,
            "value": cells[column],
        }
    else:
        headers = [header] if header else []
        locator = {**span.locator, "kind": "jats_cell", "column": column + 1, "headers": headers}
    if not unit_name.strip():
        return NO_UNIT, locator
    # Подпись таблицы называет единицы только при столбце без заголовка: иначе она может
    # подтвердить единицы соседнего столбца.
    sources = (header, cells[column]) if header else (span.caption, cells[column])
    pattern = _unit_pattern(_text(unit_name))
    if not any(pattern.search(_text(text)) for text in sources):
        return f"В заголовке столбца и ячейке нет единиц «{unit_name}».", locator
    return None, locator


def _value(item: dict, document: "SourceDocument | None", row: dict) -> dict:
    located, span = _locate(item, document)
    if span is None:
        return located
    if span.cells is None:
        problem, locator = _check_text(item, row), span.locator
    else:
        problem, locator = _check_cell(item, span, row)
    if problem is None:
        return {**located, "locator": locator}
    diagnostic = " ".join(filter(None, [located["diagnostic"], problem]))
    return {
        **located,
        "locator": locator,
        "verification_status": "needs_review",
        "diagnostic": diagnostic,
    }


# ---------- Карточка ----------

_WITH_EVIDENCE = (
    "subjects",
    "methods",
    "results",
    "conclusions",
    "author_limitations",
    "unresolved",
    "set_aside",
)


def verify(candidate: "Candidate", documents: dict[int, "SourceDocument"]) -> "Candidate":
    """Адреса и статусы всех оснований карточки; показатель `verified`, только если хотя бы
    одно основание подтверждает его значение с единицами."""

    def located(items: list[dict]) -> list[dict]:
        return [locate(item, documents.get(item["document_version_id"])) for item in items]

    content = {
        **candidate.content,
        **{
            section: [
                {**entry, "evidence": located(entry["evidence"])}
                for entry in candidate.content[section]
            ]
            for section in _WITH_EVIDENCE
        },
    }
    measures = []
    for row in candidate.measures:
        values = [
            _value(item, documents.get(item["document_version_id"]), row)
            for item in row["content"]["evidence"]
        ]
        status = (
            "verified"
            if any(item["verification_status"] == "verified" for item in values)
            else "needs_review"
        )
        measures.append(
            {
                **row,
                "verification_status": status,
                "content": {
                    **row["content"],
                    "evidence": values,
                    "context_evidence": located(row["content"]["context_evidence"]),
                },
            }
        )
    return replace(candidate, content=content, measures=measures)


# ---------- Фрагмент для показа ----------

CONTEXT_LINES = 2
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ0-9(])")


def _line(number: int, text: str, highlighted: bool) -> dict:
    return {"number": number, "text": text, "highlighted": highlighted}


def _window(lines: list[str], needle: str) -> list[dict] | None:
    """Строки с цитатой и соседние; цитата, ушедшая на следующую страницу, выделяется с
    места начала до конца страницы. Цитату не нашли — фрагмента нет."""
    keys = [_key(line) for line in lines]
    found = None
    for start in range(len(lines)):
        for end in range(start, len(lines)):
            if needle in _key("\n".join(lines[start : end + 1])):
                if found is None or end - start < found[1] - found[0]:
                    found = (start, end)
                break
    if found is None:
        tail = next(
            (
                start
                for start in range(len(lines))
                if "".join(keys[start:]) and needle.startswith(_key("\n".join(lines[start:])))
            ),
            None,
        )
        if tail is None:
            return None
        found = (tail, len(lines) - 1)
    start, end = found
    shown = range(max(0, start - CONTEXT_LINES), min(len(lines), end + CONTEXT_LINES + 1))
    return [_line(n + 1, lines[n], start <= n <= end) for n in shown]


def _row_text(cells: list[str]) -> str:
    return " | ".join(cells)


def fragment(item: dict, content: dict | None) -> list[dict] | None:
    """Реальный фрагмент версии документа по адресу основания — текст документа, а не цитата
    модели. None — адреса нет или место в содержимом не найдено: псевдофрагмент не строится."""
    locator = item.get("locator")
    if locator is None or content is None:
        return None
    needle = _key(item["quote"]).strip(".,;:")
    match locator["kind"]:
        case "pdf":
            pages = content.get("pages", [])
            page = locator["page"]
            if not needle or not 1 <= page <= len(pages):
                return None
            return _window(pages[page - 1].split("\n"), needle)
        case "jats":
            block = next(
                (b for b in content.get("blocks", []) if b["path"] == locator["path"]), None
            )
            if block is None or not needle:
                return None
            return _window(_SENTENCE.split(block["text"]), needle)
        case "jats_row" | "jats_cell":
            table = next(
                (t for t in content.get("tables", []) if t["path"] == locator["path"]), None
            )
            rows = table.get("rows", []) if table else []
            row = locator["row"]
            if not 1 <= row <= len(rows):
                return None
            header = [_line(1, _row_text(rows[0]), False)] if row > 1 else []
            return [*header, _line(row, _row_text(rows[row - 1]), True)]
        case "csv":
            records = content.get("rows", [])
            record = locator["record"]
            if not 1 <= record <= len(records):
                return None
            # Заголовок — запись 0: номер записи не равен физической строке файла.
            return [
                _line(0, _row_text(content.get("header", [])), False),
                _line(record, _row_text(records[record - 1]), True),
            ]
    return None
