"""Разбор документов и проверка их идентичности по содержимому (spec «Разбор», ADR 0004).

pypdf — базовый путь цифрового PDF, структурированный разбор JATS и CSV. Успешный разбор не
доказывает точность ячеек или поддержку сканов: страницы без текстового слоя дают `partial`,
файл без текста — `unsupported`, а не пустой «разобранный» документ. Разобранное содержимое
сохраняет адреса для будущих оснований: физическую страницу PDF, путь или ID узла JATS,
параметры чтения CSV.
"""

import csv
import io
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pypdf

Format = Literal["pdf", "jats", "csv", "other"]
ParseStatus = Literal["parsed", "partial", "unsupported", "error"]
IdentityStatus = Literal["pending", "confirmed", "needs_review", "mismatch"]

PDF_PARSER = f"pypdf {pypdf.__version__}"
JATS_PARSER = "jats 1"
CSV_PARSER = "csv 2"
XLINK = "{http://www.w3.org/1999/xlink}href"
# Страницы статьи ищутся в начале файла: допустима обложка перед первой страницей.
ARTICLE_PAGES = 3
# Малый CSV (таблица результатов приложения) хранит записи для запроса и адреса ячейки;
# у большого исходного массива — только заголовки.
SMALL_CSV = 50


@dataclass(frozen=True)
class Parsed:
    format: Format
    status: ParseStatus
    detail: str | None
    parser: str | None
    content: dict | None = None


@dataclass(frozen=True)
class Work:
    """Сведения выбранной публикации, с которыми сверяется файл."""

    title: str
    doi: str | None
    pmid: str | None
    pmcid: str | None


def detect(data: bytes, name: str) -> Format:
    if b"%PDF-" in data[:1024]:
        return "pdf"
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<") and b"<article" in head:
        return "jats"
    if Path(name).suffix.lower() in (".csv", ".tsv"):
        return "csv"
    return "other"


def parse(data: bytes, name: str) -> Parsed:
    match detect(data, name):
        case "pdf":
            return _pdf(data)
        case "jats":
            return _jats(data)
        case "csv":
            return _csv(data, name)
    suffix = Path(name).suffix.lstrip(".").upper() or "файла"
    return Parsed(
        "other",
        "unsupported",
        f"Формат {suffix} не читается: поддерживаются PDF, JATS XML и CSV.",
        None,
    )


# ---------- PDF ----------


def _clean(text: str) -> str:
    # PostgreSQL не хранит NUL в тексте JSONB; pypdf иногда его возвращает.
    return text.replace("\x00", "")


def _pdf(data: bytes) -> Parsed:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return Parsed("pdf", "error", "PDF защищён паролем.", PDF_PARSER)
        pages = [_clean(page.extract_text() or "") for page in reader.pages]
    except Exception as error:  # noqa: BLE001 — pypdf бросает разные ошибки повреждённого файла
        return Parsed("pdf", "error", f"PDF не прочитан: {type(error).__name__}.", PDF_PARSER)
    if not pages:
        return Parsed("pdf", "error", "В PDF нет страниц.", PDF_PARSER)
    empty = [number for number, text in enumerate(pages, 1) if not text.strip()]
    content = {"pages": pages}
    if len(empty) == len(pages):
        detail = "В PDF нет текстового слоя: сканы без распознавания не поддерживаются."
        return Parsed("pdf", "unsupported", detail, PDF_PARSER, content)
    if empty:
        listed = ", ".join(map(str, empty))
        detail = f"Страницы без текстового слоя: {listed}. Сведения с них не извлекаются."
        return Parsed("pdf", "partial", detail, PDF_PARSER, content)
    return Parsed("pdf", "parsed", None, PDF_PARSER, content)


# ---------- JATS ----------


def _text(node: ElementTree.Element) -> str:
    return " ".join("".join(node.itertext()).split())


def _walk(node: ElementTree.Element, path: str, blocks: list[dict], tables: list[dict]) -> None:
    """Абзацы и заголовки с путём узла; таблицы — отдельно, по строкам и ячейкам."""
    seen: dict[str, int] = {}
    for child in node:
        if not isinstance(child.tag, str):
            continue
        seen[child.tag] = seen.get(child.tag, 0) + 1
        child_path = f"{path}/{child.tag}[{seen[child.tag]}]"
        if child.tag == "table-wrap":
            tables.append(_table(child, child_path))
        elif child.tag in ("title", "p"):
            if text := _text(child):
                blocks.append(
                    {"path": child_path, "id": child.get("id"), "kind": child.tag, "text": text}
                )
            _walk_tables(child, child_path, tables)
        else:
            _walk(child, child_path, blocks, tables)


def _walk_tables(node: ElementTree.Element, path: str, tables: list[dict]) -> None:
    seen: dict[str, int] = {}
    for child in node:
        if not isinstance(child.tag, str):
            continue
        seen[child.tag] = seen.get(child.tag, 0) + 1
        child_path = f"{path}/{child.tag}[{seen[child.tag]}]"
        if child.tag == "table-wrap":
            tables.append(_table(child, child_path))
        else:
            _walk_tables(child, child_path, tables)


def _table(node: ElementTree.Element, path: str) -> dict:
    label, caption = node.find("label"), node.find("caption")
    rows = [[_text(cell) for cell in row if cell.tag in ("td", "th")] for row in node.iter("tr")]
    return {
        "path": path,
        "id": node.get("id"),
        "label": _text(label) if label is not None else None,
        "caption": _text(caption) if caption is not None else None,
        "rows": rows,
    }


def _supplements(root: ElementTree.Element) -> list[dict]:
    """Приложения, перечисленные в статье; одно имя файла — одно приложение."""
    found: dict[str, dict] = {}
    for node in root.iter("supplementary-material"):
        media = node.find("media")
        href = node.get(XLINK) or (media.get(XLINK) if media is not None else None)
        if not href or href in found:
            continue
        label = node.find("label")
        found[href] = {
            "id": node.get("id"),
            "label": _text(label) if label is not None else None,
            "href": href,
        }
    return list(found.values())


def _jats(data: bytes) -> Parsed:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        return Parsed("jats", "error", f"XML не прочитан: {error}.", JATS_PARSER)
    if root.tag != "article":
        return Parsed("jats", "unsupported", "XML не является статьёй JATS.", JATS_PARSER)
    ids: dict[str, list[str]] = {}
    for node in root.iterfind("front/article-meta/article-id"):
        kind = node.get("pub-id-type") or ""
        ids.setdefault(kind, []).append(_text(node).lower())
    title = root.find("front/article-meta/title-group/article-title")
    blocks: list[dict] = []
    tables: list[dict] = []
    body = root.find("body")
    if body is not None:
        _walk(body, "/article/body[1]", blocks, tables)
    floats = root.find("floats-group")
    if floats is not None:
        _walk_tables(floats, "/article/floats-group[1]", tables)
    content = {
        "ids": ids,
        "title": _text(title) if title is not None else None,
        "blocks": blocks,
        "tables": tables,
        "supplements": _supplements(root),
    }
    if not blocks:
        detail = "В XML нет основного текста статьи: только метаданные и аннотация."
        return Parsed("jats", "partial", detail, JATS_PARSER, content)
    return Parsed("jats", "parsed", None, JATS_PARSER, content)


# ---------- CSV ----------


def _csv(data: bytes, name: str) -> Parsed:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return Parsed("csv", "error", "CSV не прочитан: кодировка файла не UTF-8.", CSV_PARSER)
    if not text.strip():
        return Parsed("csv", "error", "CSV пуст.", CSV_PARSER)
    tab = Path(name).suffix.lower() == ".tsv"
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters="\t" if tab else ",;\t")
    except csv.Error:
        dialect = csv.excel_tab if tab else csv.excel
    try:
        # Запись CSV не равна физической строке: ячейка может занимать несколько строк.
        records = list(csv.reader(io.StringIO(text, newline=""), dialect))
    except csv.Error as error:
        return Parsed("csv", "error", f"CSV не прочитан: {error}.", CSV_PARSER)
    header, rows = records[0], records[1:]
    content = {
        "encoding": "utf-8",
        "dialect": {
            "delimiter": dialect.delimiter,
            "quotechar": dialect.quotechar,
            "doublequote": dialect.doublequote,
            "skipinitialspace": dialect.skipinitialspace,
        },
        "header": header,
        "records": len(rows),
    }
    if len(rows) <= SMALL_CSV:
        content["rows"] = rows
    ragged = [number for number, row in enumerate(rows, 1) if row and len(row) != len(header)]
    if ragged:
        detail = (
            f"Записей с другим числом полей, чем в заголовке: {len(ragged)} "
            f"(первая — запись {ragged[0]})."
        )
        return Parsed("csv", "partial", detail, CSV_PARSER, content)
    return Parsed("csv", "parsed", None, CSV_PARSER, content)


# ---------- Идентичность ----------

_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
# Список литературы не подтверждает идентичность: текст после его заголовка не учитывается.
_REFERENCES = re.compile(
    r"^\s*(references|references and notes|literature cited|bibliography)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _squash(text: str) -> str:
    """Только буквы и цифры: переносы строк и дефисы PDF не мешают сравнению."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def _dois(text: str) -> list[str]:
    return [doi.rstrip(".,;)]").lower() for doi in _DOI.findall(text)]


def _head(pages: list[str]) -> list[str]:
    """Первые страницы файла до заголовка списка литературы."""
    head = []
    for page in pages[:ARTICLE_PAGES]:
        cut = _REFERENCES.search(page)
        head.append(page[: cut.start()] if cut else page)
        if cut:
            break
    return head


def identity(parsed: Parsed, work: Work) -> tuple[IdentityStatus, str]:
    """Относится ли основной текст к выбранной работе, по содержимому файла."""
    if parsed.content is None or parsed.status in ("unsupported", "error"):
        return "needs_review", "Содержимое файла не прочитано: подтвердите принадлежность вручную."
    if parsed.format == "jats":
        return _jats_identity(parsed.content, work)
    if parsed.format == "pdf":
        return _pdf_identity(parsed.content["pages"], work)
    return "needs_review", "По содержимому файла принадлежность не проверить: подтвердите вручную."


def _jats_identity(content: dict, work: Work) -> tuple[IdentityStatus, str]:
    ids = content["ids"]
    expected = {"doi": work.doi, "pmid": work.pmid, "pmcid": work.pmcid}
    matched, conflicting = [], []
    for kind, value in expected.items():
        if not value or not ids.get(kind):
            continue
        (matched if value.lower() in ids[kind] else conflicting).append(kind)
    names = {"doi": "DOI", "pmid": "PMID", "pmcid": "PMCID"}
    if matched and not conflicting:
        listed = ", ".join(names[kind] for kind in matched)
        verb = "совпадает" if len(matched) == 1 else "совпадают"
        return "confirmed", f"{listed} в метаданных статьи {verb} с выбранной работой."
    if conflicting and not matched:
        kind = conflicting[0]
        return (
            "mismatch",
            f"{names[kind]} в метаданных статьи — {ids[kind][0]}, у выбранной работы — "
            f"{expected[kind]}.",
        )
    if conflicting:
        return "needs_review", "Идентификаторы статьи совпадают с выбранной работой частично."
    title = content.get("title")
    if title and _squash(title) == _squash(work.title):
        return "confirmed", "Название статьи совпадает с выбранной работой."
    return "needs_review", "Идентификаторы статьи не указаны: подтвердите принадлежность вручную."


def _pdf_identity(pages: list[str], work: Work) -> tuple[IdentityStatus, str]:
    head = _head(pages)
    title = _squash(work.title)
    for number, page in enumerate(head, 1):
        if title and title in _squash(page):
            return "confirmed", f"Название работы найдено на странице {number} файла."
    doi = work.doi.lower() if work.doi else None
    for number, page in enumerate(head, 1):
        if doi and any(found.startswith(doi) for found in _dois(page)):
            return "confirmed", f"DOI работы указан на странице {number} файла."
    other = _dois(head[0]) if head else []
    if doi and other:
        # Чужой DOI может стоять на обложке или в ссылке: несовпадение решает пользователь.
        return (
            "needs_review",
            f"На первой странице файла указан DOI {other[0]}, у выбранной работы — {doi}: "
            "подтвердите принадлежность вручную.",
        )
    return (
        "needs_review",
        "Название и DOI работы на первых страницах файла не найдены: "
        "подтвердите принадлежность вручную.",
    )
