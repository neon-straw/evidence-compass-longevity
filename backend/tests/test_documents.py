"""Документы выбранных работ на HTTP API (#13, AC04, AC05).

Каждое получение и ручная загрузка создают неизменяемую версию с SHA-256; статусы
получения, идентичности и разбора видны раздельно. Работа без полного текста остаётся
в таблице, документ с несовпадающей идентичностью не допускает запуск обработки.
"""

import hashlib

import httpx
import pytest

from tests.conftest import settings_for_tests
from tests.fake_documents import EPMC_REST, UNPAYWALL, archive, jats, pdf, unpaywall
from tests.test_question import account  # noqa: F401
from tests.test_search import confirmed, searched  # noqa: F401
from tests.test_selection import epmc, record, select, selection

RUBY_DOI = "10.7554/elife.31157"
RUBY_PMCID = "PMC5783610"
RUBY_TITLE = "Naked Mole-Rat mortality rates defy gompertzian laws by not increasing with age."
FULL_TEXT = f"{EPMC_REST}/{RUBY_PMCID}/fullTextXML"
SUPPLEMENTS = f"{EPMC_REST}/{RUBY_PMCID}/supplementaryFiles"
# Работа без PMCID: полный текст доступен только через Unpaywall.
OTHER_DOI = "10.1234/work.1"
OTHER_TITLE = "Lifespan of captive animals, cohort 1"
OTHER_PDF = "https://publisher.test/articles/work-1.pdf"
RUBY_SUPPLEMENTS = (
    "elife-31157-supp1.csv",
    "elife-31157-transrepform.docx",
    "elife-31157-fig1.tif",
    "missing.csv",
)
CSV = b'animal_id,age_years\nA1,30.1\nA2,"12\n(estimated)"\n'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ruby_pdf() -> bytes:
    # Обложка перед страницей статьи: название работы находится на второй странице.
    return pdf(
        "Science cover\nThis issue",
        "Naked mole-rat mortality rates defy\nGompertzian laws by not increasing with age\n"
        "J. Graham Ruby",
    )


async def extraction(client) -> dict:
    response = await client.get("/api/extraction")
    assert response.status_code == 200, response.text
    return response.json()


def document(found: dict, name: str) -> dict:
    return next(item for item in found["documents"] if item["name"] == name)


def main(found: dict) -> dict:
    return next(item for item in found["documents"] if item["role"] == "main")


def statuses(row: dict) -> tuple:
    return (row["status"], row["fetch_status"], row["identity_status"], row["parse_status"])


async def at_documents(client, run_worker) -> None:  # noqa: F811
    """Поиск, выбор единственной найденной работы и переход к документам."""
    await searched(client, run_worker)
    (work,) = (await selection(client))["publications"]
    assert (await select(client, work)).status_code == 200
    assert (await client.post("/api/selection/proceed")).status_code == 200


async def upload(client, row: dict, name: str, data: bytes) -> httpx.Response:
    return await client.post(f"/api/documents/{row['id']}/upload", files={"file": (name, data)})


@pytest.fixture
async def ruby(confirmed, run_worker, sources):  # noqa: F811
    """Выбрана ruby2018: JATS и архив приложений в Europe PMC."""
    sources.documents[FULL_TEXT] = jats(
        doi=RUBY_DOI,
        pmcid=RUBY_PMCID,
        supplements=RUBY_SUPPLEMENTS,
    )
    sources.documents[SUPPLEMENTS] = archive(
        {
            "elife-31157-supp1.csv": CSV,
            "elife-31157-transrepform.docx": b"PK\x03\x04docx",
            "elife-31157-fig1.tif": b"II*\x00tiff",
        }
    )
    await at_documents(confirmed, run_worker)
    return confirmed


@pytest.fixture
async def other(confirmed, run_worker, sources):  # noqa: F811
    """Выбрана работа без PMCID и без открытой версии."""
    sources.pubmed, sources.europe_pmc = "empty", epmc(record(1))
    await at_documents(confirmed, run_worker)
    return confirmed


async def test_main_text_is_fetched_by_the_worker_with_listed_supplements(ruby, run_worker):
    before = main(await extraction(ruby))
    assert (before["name"], before["version"], before["status"]) == (
        "Основной текст",
        None,
        "fetching",
    )
    assert before["allowed_actions"] == []

    await run_worker()
    found = await extraction(ruby)

    xml = main(found)
    assert statuses(xml) == ("ready", "available", "confirmed", "parsed")
    assert (xml["name"], xml["version"], xml["acquisition"]) == (f"{RUBY_PMCID}.xml", 1, "fetched")
    assert xml["sha256"] == sha(
        jats(
            doi=RUBY_DOI,
            pmcid=RUBY_PMCID,
            supplements=RUBY_SUPPLEMENTS,
        )
    )
    assert xml["publication_label"] == f"Ruby 2018 · {RUBY_TITLE}"
    assert xml["allowed_actions"] == ["retry_fetch", "upload"]
    # Приложения — самостоятельные документы со своими версиями и статусами.
    table = document(found, "elife-31157-supp1.csv")
    assert (table["role"], table["version"], table["sha256"]) == ("supplement", 1, sha(CSV))
    assert statuses(table) == ("ready", "available", "confirmed", "parsed")
    # Форматы, которые не читаются (всё, кроме PDF, JATS XML и CSV), не загружаются:
    # приложение сразу «Не читается» с причиной, повторять загрузку бессмысленно.
    form = document(found, "elife-31157-transrepform.docx")
    assert statuses(form) == ("not_parsed", "unavailable", None, None)
    assert form["status_detail"] == (
        "Формат DOCX не читается: сохраните таблицу как CSV или PDF и загрузите новой версией."
    )
    assert form["allowed_actions"] == ["upload"]
    figure = document(found, "elife-31157-fig1.tif")
    assert statuses(figure) == ("not_parsed", "unavailable", None, None)
    assert figure["status_detail"] == "Изображение: значения из него не извлекаются."
    assert figure["allowed_actions"] == ["upload"]
    missing = document(found, "missing.csv")
    assert statuses(missing) == ("not_found", "unavailable", None, None)
    assert "отсутствует в архиве" in missing["status_detail"]
    assert found["startable_publication_ids"] == [xml["publication_id"]]
    assert found["start_blocked_reason"] is None
    assert found["operation"]["status"] == "not_started"


async def test_unreadable_supplements_are_not_requested(confirmed, run_worker, sources):  # noqa: F811
    listed = ("fig1.tif", "fig2.png", "table.xlsx", "video.mp4")
    sources.documents[FULL_TEXT] = jats(doi=RUBY_DOI, pmcid=RUBY_PMCID, supplements=listed)
    await at_documents(confirmed, run_worker)

    await run_worker()
    found = await extraction(confirmed)

    assert not [
        request for request in sources.requests if request.url.path.endswith("/supplementaryFiles")
    ]
    assert {document(found, name)["status"] for name in listed} == {"not_parsed"}
    assert document(found, "video.mp4")["status_detail"] == (
        "Формат MP4 не читается: поддерживаются PDF, JATS XML и CSV."
    )
    retried = await confirmed.post(
        f"/api/documents/{document(found, 'table.xlsx')['id']}/retry-fetch"
    )
    assert retried.status_code == 409


async def test_retry_and_manual_upload_create_new_versions(ruby, run_worker):
    await run_worker()
    xml = main(await extraction(ruby))

    retried = await ruby.post(f"/api/documents/{xml['id']}/retry-fetch")
    assert retried.status_code == 200, retried.text
    assert (retried.json()["status"], retried.json()["version"]) == ("fetching", 1)
    await run_worker()
    again = main(await extraction(ruby))
    # Тот же файл — новая версия, а не незаметная замена прежней.
    assert (again["version"], again["sha256"]) == (2, xml["sha256"])

    uploaded = await upload(ruby, again, "ruby2018.pdf", ruby_pdf())
    assert uploaded.status_code == 200, uploaded.text
    row = uploaded.json()
    assert (row["name"], row["version"], row["acquisition"]) == ("ruby2018.pdf", 3, "uploaded")
    assert row["sha256"] == sha(ruby_pdf())
    assert statuses(row) == ("ready", "available", "confirmed", "parsed")
    assert row["status_detail"] is None
    # Обновление основного текста не трогает приложения.
    found = await extraction(ruby)
    assert document(found, "elife-31157-supp1.csv")["version"] == 1
    assert len(found["documents"]) == 5


async def test_work_without_full_text_stays_visible(other, run_worker):
    await run_worker()
    found = await extraction(other)

    (row,) = found["documents"]
    assert (row["name"], row["publication_label"]) == (
        "Основной текст",
        f"Author 2020 · {OTHER_TITLE}",
    )
    assert statuses(row) == ("not_found", "unavailable", None, None)
    assert row["status_detail"] == (
        "Полный текст не загружен: Europe PMC — у работы нет PMCID; "
        "Unpaywall не настроен (EC_UNPAYWALL_EMAIL)."
    )
    assert row["allowed_actions"] == ["retry_fetch", "upload"]
    assert found["startable_publication_ids"] == []
    assert found["start_blocked_reason"].startswith("Нет работ с готовым основным текстом")
    assert found["operation"]["allowed_actions"] == []

    uploaded = await upload(other, row, "work-1.pdf", pdf(f"{OTHER_TITLE}\nAuthor A."))
    assert statuses(uploaded.json()) == ("ready", "unavailable", "confirmed", "parsed")
    assert (await extraction(other))["startable_publication_ids"] == [row["publication_id"]]


async def test_open_pdf_is_found_through_unpaywall(confirmed, run_worker, sources):  # noqa: F811
    sources.pubmed, sources.europe_pmc = "empty", epmc(record(1))
    sources.documents[f"{UNPAYWALL}/{OTHER_DOI}"] = unpaywall(
        "https://landing.test/work-1", OTHER_PDF
    )
    sources.documents["https://landing.test/work-1"] = b"<html>Sign in</html>"
    sources.documents[OTHER_PDF] = pdf(f"{OTHER_TITLE}\ndoi:{OTHER_DOI}")
    await at_documents(confirmed, run_worker)

    # Посадочная страница вместо PDF пропускается, берётся следующий открытый адрес.
    await run_worker(settings_for_tests(unpaywall_email="research@example.org"))
    row = main(await extraction(confirmed))

    assert statuses(row) == ("ready", "available", "confirmed", "parsed")
    assert (row["name"], row["version"]) == ("work-1.pdf", 1)


async def test_service_failure_differs_from_missing_full_text(ruby, run_worker, sources):
    sources.documents[FULL_TEXT] = 503
    await run_worker()

    row = main(await extraction(ruby))
    assert statuses(row) == ("error", "error", None, None)
    assert row["status_detail"].startswith("Полный текст не загружен: Europe PMC — ответ 503")
    assert "retry_fetch" in row["allowed_actions"]


async def test_closed_work_without_open_pdf_is_not_found_not_an_error(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    """Vijg 2023: Europe PMC закрыл текст (`isOpenAccess: N`) и отвечает 500, а PDF в PMC
    открывается только в браузере — это отсутствие открытого текста, а не сбой (#43)."""
    pmcid = "PMC10071417"
    stub = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/work-1.pdf"
    sources.pubmed = "empty"
    sources.europe_pmc = epmc(record(1, pmcid=pmcid, isOpenAccess="N"))
    sources.documents[f"{EPMC_REST}/{pmcid}/fullTextXML"] = 500
    sources.documents[f"{UNPAYWALL}/{OTHER_DOI}"] = unpaywall(stub)
    sources.documents[stub] = b"<html>Preparing to download ...</html>"
    await at_documents(confirmed, run_worker)

    await run_worker(settings_for_tests(unpaywall_email="research@example.org"))
    row = main(await extraction(confirmed))

    assert statuses(row) == ("not_found", "unavailable", None, None)
    assert row["status_detail"] == (
        "Полный текст не загружен: Europe PMC — открытого полного текста нет; "
        "Unpaywall — PMC отдаёт этот PDF только в браузере."
    )
    # Экран предлагает открыть PDF и загрузить его вручную.
    assert row["source_url"] == stub
    assert "upload" in row["allowed_actions"]

    uploaded = await upload(confirmed, row, "work-1.pdf", pdf(f"{OTHER_TITLE}\nAuthor A."))
    assert (uploaded.json()["status"], uploaded.json()["source_url"]) == ("ready", None)


async def test_failed_retry_keeps_the_current_version(ruby, run_worker, sources):
    await run_worker()
    xml = main(await extraction(ruby))
    sources.documents[FULL_TEXT] = 404

    await ruby.post(f"/api/documents/{xml['id']}/retry-fetch")
    await run_worker()

    row = main(await extraction(ruby))
    assert (row["version"], row["status"], row["fetch_status"]) == (1, "ready", "unavailable")
    assert row["status_detail"] == "Повторная загрузка не удалась, используется версия 1."


async def test_foreign_doi_in_references_does_not_confirm_identity(other, run_worker):
    await run_worker()
    row = main(await extraction(other))
    # Чужая статья цитирует выбранную работу: название и DOI только в списке литературы.
    citing = pdf(
        "Survival of wild elephants\nSmith B.",
        f"References\n1. {OTHER_TITLE}. doi:{OTHER_DOI}",
    )

    uploaded = (await upload(other, row, "citing.pdf", citing)).json()

    assert statuses(uploaded) == ("needs_review", "unavailable", "needs_review", "parsed")
    assert uploaded["allowed_actions"] == ["retry_fetch", "upload", "confirm_identity"]
    assert (await extraction(other))["startable_publication_ids"] == []


async def test_partially_parsed_main_text_is_usable_but_not_shown_as_ready(other, run_worker):
    await run_worker()
    row = main(await extraction(other))

    uploaded = (await upload(other, row, "scan.pdf", pdf(OTHER_TITLE, ""))).json()

    assert statuses(uploaded) == ("needs_review", "unavailable", "confirmed", "partial")
    assert (await extraction(other))["startable_publication_ids"] == [row["publication_id"]]


async def test_identity_mismatch_blocks_the_document_until_the_user_decides(other, run_worker):
    await run_worker()
    row = main(await extraction(other))
    wrong = pdf("Growth of sharks\ndoi:10.9999/sharks.2", "Methods")

    uploaded = (await upload(other, row, "wrong.pdf", wrong)).json()
    # Чужой DOI на первой странице — повод для проверки, а не автоматический вердикт.
    assert statuses(uploaded) == ("needs_review", "unavailable", "needs_review", "parsed")
    assert uploaded["status_detail"] == (
        f"На первой странице файла указан DOI 10.9999/sharks.2, у выбранной работы — {OTHER_DOI}: "
        "подтвердите принадлежность вручную."
    )
    assert (await extraction(other))["startable_publication_ids"] == []

    rejected = await other.post(
        f"/api/documents/{row['id']}/identity", json={"belongs_to_publication": False}
    )
    assert statuses(rejected.json()) == ("error", "unavailable", "mismatch", "parsed")
    assert rejected.json()["status_detail"] == (
        "Пользователь указал, что файл не относится к выбранной работе."
    )
    assert (await extraction(other))["startable_publication_ids"] == []

    accepted = await other.post(
        f"/api/documents/{row['id']}/identity", json={"belongs_to_publication": True}
    )
    assert statuses(accepted.json()) == ("ready", "unavailable", "confirmed", "parsed")
    assert (await extraction(other))["startable_publication_ids"] == [row["publication_id"]]


async def test_manual_supplement_needs_the_users_confirmation(ruby, run_worker):
    await run_worker()
    table = document(await extraction(ruby), "elife-31157-supp1.csv")

    uploaded = (await upload(ruby, table, "supp1-corrected.csv", CSV)).json()

    assert (uploaded["version"], uploaded["acquisition"]) == (2, "uploaded")
    assert statuses(uploaded) == ("needs_review", "available", "needs_review", "parsed")
    assert "confirm_identity" in uploaded["allowed_actions"]


@pytest.mark.parametrize(
    ("name", "data", "status", "parse_status", "detail"),
    [
        (
            "scan.pdf",
            pdf(f"{OTHER_TITLE}", ""),
            "needs_review",
            "partial",
            "Страницы без текстового слоя: 2. Сведения с них не извлекаются.",
        ),
        (
            "scan.pdf",
            pdf("", ""),
            "needs_review",
            "unsupported",
            "Содержимое файла не прочитано: подтвердите принадлежность вручную.",
        ),
        ("broken.pdf", b"%PDF-1.4\nnot a pdf", "error", "error", None),
        ("table.csv", "возраст;лет\n".encode("cp1251"), "error", "error", None),
        ("tables.xlsx", b"PK\x03\x04xlsx", "needs_review", "unsupported", None),
    ],
    ids=["partial-pdf", "scan", "broken-pdf", "not-utf8-csv", "xlsx"],
)
async def test_parse_statuses_stay_visible(
    other, run_worker, name, data, status, parse_status, detail
):
    await run_worker()
    row = main(await extraction(other))

    uploaded = (await upload(other, row, name, data)).json()

    assert (uploaded["status"], uploaded["parse_status"]) == (status, parse_status)
    assert uploaded["status_detail"]
    if detail:
        assert uploaded["status_detail"] == detail


async def test_scanned_pdf_is_unsupported_not_parsed(other, run_worker):
    await run_worker()
    row = main(await extraction(other))

    uploaded = (await upload(other, row, "scan.pdf", pdf("", ""))).json()
    # Принадлежность подтверждает пользователь; пустой разбор всё равно не становится «Готов».
    confirmed_row = (
        await other.post(
            f"/api/documents/{row['id']}/identity", json={"belongs_to_publication": True}
        )
    ).json()

    assert uploaded["parse_status"] == "unsupported"
    assert confirmed_row["status"] == "needs_review"
    assert confirmed_row["status_detail"].startswith("В PDF нет текстового слоя")


async def test_document_changes_wait_for_a_free_slot(ruby, run_worker):
    await run_worker()
    xml = main(await extraction(ruby))
    # Новый поиск занимает общий слот: документы доступны только для чтения.
    assert (await ruby.post("/api/search/start")).status_code == 202

    found = await extraction(ruby)
    assert main(found)["allowed_actions"] == []
    assert found["start_blocked_reason"].startswith("Идёт поиск")
    for response in (
        await ruby.post(f"/api/documents/{xml['id']}/retry-fetch"),
        await upload(ruby, xml, "ruby2018.pdf", ruby_pdf()),
        await ruby.post(
            f"/api/documents/{xml['id']}/identity", json={"belongs_to_publication": False}
        ),
    ):
        assert response.status_code == 409
        assert response.json()["message"].startswith("Идёт поиск")
    assert main(await extraction(ruby))["version"] == 1


async def test_document_in_fetch_cannot_be_replaced(ruby):
    xml = main(await extraction(ruby))

    response = await upload(ruby, xml, "ruby2018.pdf", ruby_pdf())

    assert response.status_code == 409
    assert response.json() == {
        "target": xml["id"],
        "message": "Документ загружается: дождитесь завершения.",
    }


async def test_unknown_document_is_not_found(ruby):
    for path in ("/api/documents/999/retry-fetch", "/api/documents/abc/retry-fetch"):
        assert (await ruby.post(path)).status_code == 404


async def test_work_selected_after_proceeding_gets_its_documents(ruby, run_worker, sources):
    await run_worker()
    sources.pubmed, sources.europe_pmc = "empty", epmc(record(1))
    await searched(ruby, run_worker)
    work = next(
        item for item in (await selection(ruby))["publications"] if item["title"] == OTHER_TITLE
    )

    assert (await select(ruby, work)).status_code == 200
    await run_worker()

    found = await extraction(ruby)
    labels = {row["publication_label"] for row in found["documents"] if row["role"] == "main"}
    assert labels == {f"Ruby 2018 · {RUBY_TITLE}", f"Author 2020 · {OTHER_TITLE}"}


def work(found: dict, publication_id: str) -> dict:
    return next(item for item in found["works"] if item["publication_id"] == publication_id)


async def test_documents_are_grouped_by_work_with_its_state_and_gaps(ruby, run_worker):
    fetching = (await extraction(ruby))["works"]
    assert [(item["state"], item["document_count"]) for item in fetching] == [("fetching", 1)]

    await run_worker()
    found = await extraction(ruby)

    (ruby_work,) = found["works"]
    assert ruby_work == {
        "publication_id": main(found)["publication_id"],
        "label": f"Ruby 2018 · {RUBY_TITLE}",
        "document_count": 5,
        "state": "ready",
        # Изображение без разбора в пробелы не входит.
        "gaps": ["1 приложение не загружено", "1 приложение с таблицами не прочитано"],
        "allowed_actions": ["upload_supplement"],
    }


async def test_work_state_follows_its_main_text(other, run_worker):
    await run_worker()
    row = main(await extraction(other))
    assert work(await extraction(other), row["publication_id"])["state"] == "no_full_text"

    await upload(other, row, "wrong.pdf", pdf("Growth of sharks\ndoi:10.9999/sharks.2"))
    assert work(await extraction(other), row["publication_id"])["state"] == "needs_review"

    await upload(other, row, "scan.pdf", pdf(OTHER_TITLE, ""))
    found = await extraction(other)
    assert work(found, row["publication_id"])["state"] == "ready"
    assert work(found, row["publication_id"])["gaps"] == ["Основной текст прочитан частично"]


async def add_supplement(client, publication_id: str, name: str, data: bytes) -> httpx.Response:
    return await client.post(
        f"/api/publications/{publication_id}/supplements", files={"file": (name, data)}
    )


async def test_user_adds_a_supplement_the_system_did_not_find(other, run_worker):
    await run_worker()
    publication_id = main(await extraction(other))["publication_id"]

    added = await add_supplement(other, publication_id, "survival.csv", CSV)

    assert added.status_code == 200, added.text
    row = added.json()
    assert (row["name"], row["role"], row["version"], row["acquisition"]) == (
        "survival.csv",
        "supplement",
        1,
        "uploaded",
    )
    # Не получался — статуса получения нет; принадлежность подтверждает пользователь.
    assert statuses(row) == ("needs_review", None, "needs_review", "parsed")
    assert row["allowed_actions"] == ["upload", "confirm_identity"]
    retry = await other.post(f"/api/documents/{row['id']}/retry-fetch")
    assert retry.status_code == 409
    assert retry.json()["message"].startswith("Приложение загружено вручную")
    found = await extraction(other)
    assert work(found, publication_id)["document_count"] == 2
    assert work(found, publication_id)["gaps"] == ["1 приложение требует проверки"]


async def test_supplement_is_added_only_to_a_selected_work_with_a_free_slot(ruby, run_worker):
    await run_worker()
    publication_id = main(await extraction(ruby))["publication_id"]

    assert (await add_supplement(ruby, "999", "a.csv", CSV)).status_code == 404
    assert (await ruby.post("/api/search/start")).status_code == 202
    busy = await add_supplement(ruby, publication_id, "a.csv", CSV)
    assert busy.status_code == 409
    assert busy.json()["message"].startswith("Идёт поиск")
    assert work(await extraction(ruby), publication_id)["allowed_actions"] == []
