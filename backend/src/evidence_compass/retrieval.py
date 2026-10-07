"""Получение документов: Europe PMC (JATS и архив приложений) и Unpaywall (открытый PDF).

Проверка подключения не доказывает загрузку полного текста: отсутствие открытой версии и
отказ сервиса различаются, чтобы повтор предлагался только там, где он может помочь.
Проверка на бота (PMC отдаёт PDF только в браузере) не обходится: путь — ручная загрузка.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, urlparse

import httpx

from evidence_compass.parsing import detect

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL = "https://api.unpaywall.org/v2"
CLOSED = "Europe PMC — открытого полного текста нет"
BROWSER_ONLY = "Unpaywall — PMC отдаёт этот PDF только в браузере"


@dataclass(frozen=True)
class Obtained:
    data: bytes
    name: str
    url: str


class NotObtained(Exception):
    """Документ не получен; `transient` — сбой сервиса, повтор может помочь.

    `settled` — понятный постоянный ответ, который повтор не изменит, даже если другой
    источник отказал временно; `url` — ссылка на PDF от Unpaywall, который не загрузился:
    пользователь открывает её в браузере и загружает файл вручную.
    """

    def __init__(
        self, detail: str, *, transient: bool, settled: bool = False, url: str | None = None
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.transient = transient
        self.settled = settled
        self.url = url


def supplements_url(pmcid: str) -> str:
    return f"{EPMC}/{pmcid}/supplementaryFiles"


class Retriever:
    def __init__(
        self, client: httpx.AsyncClient, timeout: float, limit: int, unpaywall_email: str | None
    ) -> None:
        self.client = client
        self.timeout = timeout
        self.limit = limit
        self.unpaywall_email = unpaywall_email

    async def _get(self, url: str, **params: str) -> httpx.Response:
        """Ответ целиком, но не больше предела: файл читается потоком и обрывается."""
        async with self.client.stream(
            "GET", url, params=params, timeout=self.timeout, follow_redirects=True
        ) as response:
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.limit:
                    raise NotObtained(f"файл больше {self.limit // 2**20} МБ", transient=False)
                chunks.append(chunk)
        # Тело уже раскодировано: заголовки сжатия не переносятся.
        return httpx.Response(
            response.status_code, content=b"".join(chunks), request=response.request
        )

    async def main_text(
        self, doi: str | None, pmcid: str | None, open_access: bool | None = None
    ) -> Obtained:
        """Основной текст: JATS Europe PMC, иначе открытый PDF по сведениям Unpaywall.

        `open_access` — признак открытого доступа из поиска; None — неизвестен. Итог временный,
        только если хоть одна попытка отказала временно и ни одна не дала понятного
        постоянного ответа.
        """
        failures: list[NotObtained] = []
        try:
            return await self._europe_pmc(pmcid, open_access)
        except NotObtained as failure:
            failures.append(failure)
        try:
            return await self._unpaywall(doi)
        except NotObtained as failure:
            failures.append(failure)
        raise NotObtained(
            "Полный текст не загружен: " + "; ".join(f.detail for f in failures) + ".",
            transient=any(f.transient for f in failures) and not any(f.settled for f in failures),
            url=next((f.url for f in failures if f.url), None),
        )

    async def _europe_pmc(self, pmcid: str | None, open_access: bool | None) -> Obtained:
        if not pmcid:
            raise NotObtained("Europe PMC — у работы нет PMCID", transient=False)
        url = f"{EPMC}/{pmcid}/fullTextXML"
        try:
            response = await self._get(url)
        except httpx.HTTPError as error:
            raise NotObtained(f"Europe PMC — {_failure(error)}", transient=True) from error
        # На fullTextXML закрытой работы Europe PMC отвечает 500, а не 404.
        closed = open_access is False and response.status_code >= 500
        if response.status_code == 404 or closed:
            raise NotObtained(CLOSED, transient=False)
        if response.status_code != 200 or detect(response.content, "") != "jats":
            raise NotObtained(
                f"Europe PMC — ответ {response.status_code}", transient=_retry(response)
            )
        return Obtained(response.content, f"{pmcid}.xml", url)

    async def _unpaywall(self, doi: str | None) -> Obtained:
        if not doi:
            raise NotObtained("Unpaywall — у работы нет DOI", transient=False)
        if not self.unpaywall_email:
            raise NotObtained("Unpaywall не настроен (EC_UNPAYWALL_EMAIL)", transient=False)
        try:
            response = await self._get(f"{UNPAYWALL}/{quote(doi)}", email=self.unpaywall_email)
            if response.status_code == 404:
                raise NotObtained("Unpaywall — DOI неизвестен", transient=False)
            response.raise_for_status()
            record = response.json()
        except httpx.HTTPError as error:
            raise NotObtained(f"Unpaywall — {_failure(error)}", transient=True) from error
        except ValueError as error:
            raise NotObtained("Unpaywall — ответ неожиданного вида", transient=True) from error
        locations = [record.get("best_oa_location")] + list(record.get("oa_locations") or [])
        urls = list(
            dict.fromkeys(
                item["url_for_pdf"] for item in locations if item and item.get("url_for_pdf")
            )
        )
        if not urls:
            raise NotObtained("Unpaywall — открытой версии PDF нет", transient=False)
        notes, browser_only = [], None
        for url in urls:
            try:
                pdf = await self._get(url)
            except httpx.HTTPError as error:
                notes.append(_failure(error))
                continue
            if pdf.status_code == 200 and detect(pdf.content, "") == "pdf":
                return Obtained(pdf.content, _pdf_name(url, doi), url)
            if pdf.status_code == 200 and _pmc(pdf.request.url):
                browser_only = browser_only or url
                continue
            notes.append(
                f"ответ {pdf.status_code}" if pdf.status_code != 200 else "страница вместо PDF"
            )
        if browser_only:
            raise NotObtained(BROWSER_ONLY, transient=False, settled=True, url=browser_only)
        raise NotObtained(
            f"Unpaywall — открытый PDF не загружен ({', '.join(notes)})",
            transient=False,
            url=urls[0],
        )

    async def supplements(self, pmcid: str, names: list[str]) -> dict[str, bytes | None]:
        """Файлы приложений из архива Europe PMC; отсутствующие в архиве не возвращаются,
        превышающие предел размера — None."""
        try:
            response = await self._get(supplements_url(pmcid))
        except httpx.HTTPError as error:
            raise NotObtained(
                f"Архив приложений Europe PMC не загружен: {_failure(error)}.", transient=True
            ) from error
        if response.status_code == 404:
            raise NotObtained("Europe PMC не отдаёт приложения этой работы.", transient=False)
        if response.status_code != 200:
            raise NotObtained(
                f"Архив приложений Europe PMC: ответ {response.status_code}.",
                transient=_retry(response),
            )
        try:
            archive = zipfile.ZipFile(io.BytesIO(response.content))
            members = {info.filename: info for info in archive.infolist()}
            return {
                name: archive.read(members[name]) if members[name].file_size <= self.limit else None
                for name in names
                if name in members
            }
        except (zipfile.BadZipFile, OSError) as error:
            raise NotObtained("Архив приложений Europe PMC повреждён.", transient=True) from error


def _retry(response: httpx.Response) -> bool:
    return response.status_code == 429 or response.status_code >= 500


def _pmc(url: httpx.URL) -> bool:
    """Адрес PMC: вместо PDF он отдаёт проверку на бота, которой нужен JavaScript."""
    return url.host == "pmc.ncbi.nlm.nih.gov" or (
        url.host in ("www.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov") and url.path.startswith("/pmc/")
    )


def _failure(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "нет ответа"
    return f"недоступен ({type(error).__name__})"


def _pdf_name(url: str, doi: str) -> str:
    name = PurePosixPath(urlparse(url).path).name
    return name if name.lower().endswith(".pdf") else doi.replace("/", "_") + ".pdf"
