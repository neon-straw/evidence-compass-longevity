"""HTTP-адаптеры PubMed и Europe PMC: выполнение сохранённых запросов и разбор записей.

Детерминированные рендеры подтверждённых критериев находятся в search_queries.
"""

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from evidence_compass.criteria import Resource


@dataclass(frozen=True)
class FoundRecord:
    """Запись выдачи источника как есть; объединение записей — задача отбора (#12)."""

    # Идентификатор записи в источнике: PMID для PubMed, `SRC:id` для Europe PMC.
    source_id: str
    title: str
    authors: str
    year: int | None
    doi: str | None
    pmid: str | None
    pmcid: str | None
    abstract: str | None
    publication_types: list[str] = field(default_factory=list)
    # Открытый текст по сведениям источника, без загрузки; None — источник не сообщает.
    open_access: bool | None = None


@dataclass(frozen=True)
class Found:
    # Число записей, о котором сообщил источник; сохраняются не более `limit` первых.
    hit_count: int
    records: list[FoundRecord]


class SourceFailed(Exception):
    """Источник не ответил или ответил ошибкой — это не пустая выдача (AC02).

    `transient` — повтор может помочь (сеть, перегрузка, обрезанный ответ); отказ
    в разборе запроса повтором не исправить.
    """

    def __init__(self, message: str, *, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


class Source(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> Found: ...


def _error(name: str, error: httpx.HTTPError, timeout: float) -> SourceFailed:
    if isinstance(error, httpx.TimeoutException):
        return SourceFailed(f"{name} не ответил за {timeout:g} с.")
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return SourceFailed(f"{name} ответил {code}.", transient=code == 429 or code >= 500)
    return SourceFailed(f"{name} недоступен: {type(error).__name__}.")


def _year(value: object) -> int | None:
    text = str(value or "")[:4]
    return int(text) if text.isdigit() else None


def _doi(value: str | None) -> str | None:
    # DOI не различает регистр; источники отдают его по-разному.
    return value.strip().lower() if value and value.strip() else None


class PubMed:
    """E-utilities: esearch (число и PMID) и efetch (библиографические данные и аннотация)."""

    name = "PubMed"
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, client: httpx.AsyncClient, timeout: float) -> None:
        self.client = client
        self.timeout = timeout

    async def search(self, query: str, limit: int) -> Found:
        try:
            found = await self.client.get(
                f"{self.base_url}/esearch.fcgi",
                params={"db": "pubmed", "term": query, "retmode": "json", "retmax": limit},
                timeout=self.timeout,
            )
            found.raise_for_status()
            result = found.json()["esearchresult"]
            if "ERROR" in result:
                # Синтаксическая ошибка запроса приходит с count 0 — это отказ, не пустая выдача.
                raise SourceFailed(f"PubMed отклонил запрос: {result['ERROR']}", transient=False)
            count, ids = int(result["count"]), list(result["idlist"])
            if not ids:
                return Found(count, [])
            fetched = await self.client.get(
                f"{self.base_url}/efetch.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
                timeout=self.timeout,
            )
            fetched.raise_for_status()
            records = [
                _pubmed_record(item)
                for item in ElementTree.fromstring(fetched.content).iter("PubmedArticle")
            ]
        except httpx.HTTPError as error:
            raise _error(self.name, error, self.timeout) from error
        except (KeyError, ValueError, TypeError, ElementTree.ParseError) as error:
            raise SourceFailed("PubMed вернул ответ неожиданного вида.") from error
        return Found(count, records)


def _text(node: ElementTree.Element | None) -> str:
    # Заголовки и аннотации содержат разметку (<i>, <sup>): текст собирается целиком.
    return "".join(node.itertext()).strip() if node is not None else ""


def _pubmed_record(article: ElementTree.Element) -> FoundRecord:
    citation = article.find("MedlineCitation")
    if citation is None:
        raise ValueError("PubmedArticle без MedlineCitation")
    pmid = _text(citation.find("PMID"))
    body = citation.find("Article")
    ids = {
        node.get("IdType"): _text(node) for node in article.iterfind("PubmedData/ArticleIdList/*")
    }
    parts = []
    for node in citation.iterfind("Article/Abstract/AbstractText"):
        label = node.get("Label")
        parts.append(f"{label}: {_text(node)}" if label else _text(node))
    authors = []
    for node in citation.iterfind("Article/AuthorList/Author"):
        name = " ".join(filter(None, [_text(node.find("LastName")), _text(node.find("Initials"))]))
        authors.append(name or _text(node.find("CollectiveName")))
    date = citation.find("Article/Journal/JournalIssue/PubDate")
    year = None
    if date is not None:
        year = _year(_text(date.find("Year")) or _text(date.find("MedlineDate")))
    return FoundRecord(
        source_id=pmid,
        title=_text(body.find("ArticleTitle")) if body is not None else "",
        authors=", ".join(authors),
        year=year,
        doi=_doi(ids.get("doi")),
        pmid=pmid,
        pmcid=ids.get("pmc"),
        abstract="\n".join(parts) or None,
        publication_types=[
            _text(node) for node in citation.iterfind("Article/PublicationTypeList/PublicationType")
        ],
    )


class EuropePMC:
    """REST search с `resultType=core`: число, библиографические данные и аннотация сразу."""

    name = "Europe PMC"
    base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def __init__(self, client: httpx.AsyncClient, timeout: float) -> None:
        self.client = client
        self.timeout = timeout

    async def search(self, query: str, limit: int) -> Found:
        try:
            response = await self.client.get(
                f"{self.base_url}/search",
                params={
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": limit,
                    "cursorMark": "*",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            count = int(payload["hitCount"])
            items = payload.get("resultList", {}).get("result", [])
            records = [_epmc_record(item) for item in items[:limit]]
        except httpx.HTTPError as error:
            raise _error(self.name, error, self.timeout) from error
        except (KeyError, ValueError, TypeError) as error:
            raise SourceFailed("Europe PMC вернул ответ неожиданного вида.") from error
        return Found(count, records)


# Коды доступности Europe PMC: OA — открытый доступ, F — свободный; S, R — подписка, регистрация.
OPEN_CODES = {"OA", "F"}
TEXT_STYLES = {"pdf", "html", "xml"}


def _epmc_open_access(item: dict) -> bool:
    """Есть ли открытый полный текст по сведениям Europe PMC (не по свободной аннотации)."""
    urls = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
    if isinstance(urls, dict):
        urls = [urls]
    return item.get("isOpenAccess") == "Y" or any(
        (url.get("availabilityCode") or "").upper() in OPEN_CODES
        and (url.get("documentStyle") or "").lower() in TEXT_STYLES
        for url in urls
    )


def _epmc_record(item: dict) -> FoundRecord:
    return FoundRecord(
        source_id=f"{item['source']}:{item['id']}",
        title=(item.get("title") or "").strip(),
        authors=(item.get("authorString") or "").strip().rstrip("."),
        year=_year(item.get("pubYear")),
        doi=_doi(item.get("doi")),
        pmid=item.get("pmid"),
        pmcid=item.get("pmcid"),
        abstract=item.get("abstractText") or None,
        publication_types=list(item.get("pubTypeList", {}).get("pubType", [])),
        open_access=_epmc_open_access(item),
    )


SOURCES: dict[Resource, type[PubMed] | type[EuropePMC]] = {
    "pubmed": PubMed,
    "europe_pmc": EuropePMC,
}


def adapters(client: httpx.AsyncClient, timeout: float) -> dict[Resource, Source]:
    return {resource: source(client, timeout) for resource, source in SOURCES.items()}
