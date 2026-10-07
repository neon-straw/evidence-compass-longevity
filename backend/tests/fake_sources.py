"""Подменённые PubMed и Europe PMC: сохранённые ответы `data/corpus/search-responses`.

Ответы корпуса — поиск заранее известной записи по DOI, а не тематический поиск; здесь они
служат контролируемой выдачей источника (spec, «Основная граница: API»).
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

CORPUS = Path(__file__).parents[2] / "data" / "corpus" / "search-responses"

# Ответ источника: работа корпуса, "empty" — успешная пустая выдача, число — HTTP-статус,
# исключение httpx — сетевой отказ, готовый `httpx.Response` — ответ как есть, байты — файл.
Reply = str | int | bytes | httpx.HTTPError | httpx.Response


def _json(name: str) -> dict:
    return json.loads((CORPUS / name).read_text())


def _pubmed(request: httpx.Request, reply: str) -> httpx.Response:
    if request.url.path.endswith("/efetch.fcgi"):
        return httpx.Response(200, content=(CORPUS / f"{reply}-pubmed-efetch.xml").read_bytes())
    if reply == "empty":
        body = {"esearchresult": {"count": "0", "retmax": "0", "retstart": "0", "idlist": []}}
        return httpx.Response(200, json=body)
    return httpx.Response(200, json=_json(f"{reply}-pubmed.json"))


def _europe_pmc(reply: str) -> httpx.Response:
    if reply == "empty":
        return httpx.Response(200, json={"hitCount": 0, "resultList": {"result": []}})
    return httpx.Response(200, json=_json(f"{reply}-epmc.json"))


@dataclass
class FakeSources:
    pubmed: Reply = "ruby2018"
    europe_pmc: Reply = "ruby2018"
    # Документы по адресу без параметров: полный текст и приложения Europe PMC, Unpaywall,
    # файлы издателей. Прочие адреса документов отвечают 404.
    documents: dict[str, Reply] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    # Вызывается до ответа источника: так тест действует, пока запрос «в пути».
    during: Callable[[httpx.Request], Awaitable[None]] | None = None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.during:
            await self.during(request)
        address = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        pubmed = request.url.host == "eutils.ncbi.nlm.nih.gov"
        if address in self.documents:
            reply = self.documents[address]
        elif pubmed or address.endswith("/europepmc/webservices/rest/search"):
            reply = self.pubmed if pubmed else self.europe_pmc
        else:
            reply = 404
        if isinstance(reply, httpx.HTTPError):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, int):
            return httpx.Response(reply, text="service unavailable")
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return _pubmed(request, reply) if pubmed else _europe_pmc(reply)

    def queries(self, host: str) -> list[str]:
        """Текст запросов к источнику: `term` PubMed esearch или `query` Europe PMC."""
        return [
            request.url.params.get("term") or request.url.params["query"]
            for request in self.requests
            if request.url.host == host and request.url.path.endswith(("/esearch.fcgi", "/search"))
        ]
