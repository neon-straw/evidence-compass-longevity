"""Итог попыток получения основного текста: временный сбой или постоянное отсутствие (#43).

«Ошибка» с повтором — только настоящий сбой: таймаут, 429, ≥500 у открытой работы или при
неизвестном доступе. Закрытая работа и PDF, который PMC отдаёт только в браузере, — «Нет
полного текста». Внешние ответы — фейки; проверка на бота не обходится.
"""

import httpx
import pytest

from evidence_compass.retrieval import NotObtained, Obtained, Retriever
from tests.fake_documents import EPMC_REST, UNPAYWALL, pdf, unpaywall
from tests.fake_sources import FakeSources

DOI = "10.1101/cshperspect.a041200"
PMCID = "PMC10071417"
FULL_TEXT = f"{EPMC_REST}/{PMCID}/fullTextXML"
LOOKUP = f"{UNPAYWALL}/{DOI}"
PMC_PDF = f"https://pmc.ncbi.nlm.nih.gov/articles/{PMCID}/pdf/cshperspect-a041200.pdf"
PUBLISHER_PDF = "https://publisher.test/a041200.pdf"
# Проверка на бота PMC: страница, которой нужен JavaScript, вместо PDF.
STUB = b"<html><body>Preparing to download ...</body></html>"
TIMEOUT = httpx.ReadTimeout("slow")

NO_PDF = {LOOKUP: unpaywall()}
PMC_STUB = {LOOKUP: unpaywall(PMC_PDF), PMC_PDF: STUB}


async def main_text(open_access: bool | None, documents: dict) -> Obtained:
    sources = FakeSources(documents=documents)
    async with httpx.AsyncClient(transport=httpx.MockTransport(sources.handle)) as http:
        retriever = Retriever(http, 5, 2**20, "research@example.org")
        return await retriever.main_text(DOI, PMCID, open_access)


async def failure(open_access: bool | None, documents: dict) -> NotObtained:
    with pytest.raises(NotObtained) as raised:
        await main_text(open_access, documents)
    return raised.value


# (доступ по поиску, ответ Europe PMC, Unpaywall) → временный ли итог.
RULES = [
    pytest.param(False, 500, PMC_STUB, False, id="закрыта + заглушка PMC"),
    pytest.param(False, 500, NO_PDF, False, id="закрыта + нет PDF"),
    pytest.param(True, 500, NO_PDF, True, id="открыта, 500 + нет PDF"),
    pytest.param(None, 500, NO_PDF, True, id="доступ неизвестен, 500 + нет PDF"),
    pytest.param(True, 429, NO_PDF, True, id="открыта, 429 + нет PDF"),
    pytest.param(False, 429, NO_PDF, True, id="закрыта, 429 + нет PDF"),
    pytest.param(True, TIMEOUT, NO_PDF, True, id="таймаут + нет PDF"),
    pytest.param(False, TIMEOUT, NO_PDF, True, id="закрыта, таймаут + нет PDF"),
    # Заглушка — понятный постоянный ответ: повтор его не изменит, путь — ручная загрузка.
    pytest.param(True, TIMEOUT, PMC_STUB, False, id="таймаут + заглушка PMC"),
    pytest.param(True, 404, NO_PDF, False, id="404 + нет PDF"),
    pytest.param(False, 404, {LOOKUP: TIMEOUT}, True, id="404 + таймаут Unpaywall"),
]


@pytest.mark.parametrize(("open_access", "epmc", "found", "transient"), RULES)
async def test_outcome_is_transient_only_without_a_clear_permanent_answer(
    open_access, epmc, found, transient
):
    failed = await failure(open_access, {FULL_TEXT: epmc, **found})
    assert failed.transient is transient


async def test_closed_article_with_pmc_stub_explains_the_manual_path():
    failed = await failure(False, {FULL_TEXT: 500, **PMC_STUB})

    assert failed.detail == (
        "Полный текст не загружен: Europe PMC — открытого полного текста нет; "
        "Unpaywall — PMC отдаёт этот PDF только в браузере."
    )
    # Ссылку открывает пользователь: PDF скачивается в браузере и загружается вручную.
    assert failed.url == PMC_PDF


async def test_page_instead_of_pdf_keeps_the_link_found_by_unpaywall():
    failed = await failure(
        None,
        {FULL_TEXT: 404, LOOKUP: unpaywall(PUBLISHER_PDF), PUBLISHER_PDF: b"<html>Sign in</html>"},
    )
    assert "страница вместо PDF" in failed.detail
    assert failed.url == PUBLISHER_PDF


async def test_open_article_failure_has_no_link_without_unpaywall_pdf():
    failed = await failure(True, {FULL_TEXT: 503, **NO_PDF})
    assert failed.detail.startswith("Полный текст не загружен: Europe PMC — ответ 503")
    assert failed.url is None


async def test_pmc_stub_does_not_hide_a_later_open_pdf():
    obtained = await main_text(
        False,
        {
            FULL_TEXT: 500,
            LOOKUP: unpaywall(PMC_PDF, PUBLISHER_PDF),
            PMC_PDF: STUB,
            PUBLISHER_PDF: pdf("Genome instability"),
        },
    )
    assert obtained.url == PUBLISHER_PDF
