"""Операция извлечения на HTTP API: жизненный цикл (#14; spec «Отбор и состояния», AC12).

Worker обрабатывает выбранные работы по снимку входов; пауза и остановка подтверждаются на
границе между работами, ошибка одной работы не мешает другим, после сбоя worker продолжаются
только незавершённые работы. Сама обработка работы подменена (`FakeExtractor`); извлечение
моделью — `test_cards.py`.
"""

import pytest

from evidence_compass import worker
from evidence_compass.extraction import ModelUnavailable, NotConnected, WorkInput, WorkResult
from tests.fake_documents import pdf
from tests.fake_extractor import EXTRACTED, Crash
from tests.test_documents import extraction, upload
from tests.test_question import account  # noqa: F401
from tests.test_search import confirmed, searched  # noqa: F401
from tests.test_selection import epmc, record, select, selection

TITLES = [f"Lifespan of captive animals, cohort {n}" for n in (1, 2, 3)]


async def prepared(client, run_worker, sources, *records: dict, text: str = ""):
    """Выбранные работы с готовым основным текстом, загруженным вручную; `text` — текст статьи
    под названием и авторами."""
    sources.pubmed, sources.europe_pmc = "empty", epmc(*records)
    await searched(client, run_worker)
    for publication in (await selection(client))["publications"]:
        assert (await select(client, publication)).status_code == 200
    assert (await client.post("/api/selection/proceed")).status_code == 200
    await run_worker()
    found = await extraction(client)
    for row in found["documents"]:
        title = row["publication_label"].split(" · ", 1)[1]
        uploaded = await upload(client, row, "main.pdf", pdf(f"{title}\nAuthor A.\n{text}".strip()))
        assert uploaded.json()["status"] == "ready", uploaded.text
    return client


@pytest.fixture
async def works(confirmed, run_worker, sources):  # noqa: F811
    """Три выбранные работы с готовым основным текстом."""
    return await prepared(confirmed, run_worker, sources, record(1), record(2), record(3))


def snapshot(found: dict, publication_ids: list[str] | None = None) -> dict:
    """Тело запуска так, как его собирает клиент: работы и версии их пригодных документов."""
    ids = publication_ids or found["startable_publication_ids"]
    return {
        "publication_ids": ids,
        "document_versions": [
            {"document_id": row["id"], "version": row["version"]}
            for row in found["documents"]
            if row["publication_id"] in ids
            and row["identity_status"] == "confirmed"
            and row["parse_status"] in ("parsed", "partial")
        ],
    }


async def start(client, key: str = "key-1", body: dict | None = None):
    body = body or snapshot(await extraction(client))
    return await client.post("/api/extraction/start", json=body, headers={"Idempotency-Key": key})


def runs(found: dict) -> list[tuple]:
    return [
        (work["label"].split(" · ", 1)[1], work["execution"], work["outcome"])
        for work in found["operation"]["works"]
    ]


async def test_started_works_are_processed_by_the_worker(works, run_worker, extractor):
    before = await extraction(works)
    assert before["operation"]["status"] == "not_started"
    assert before["operation"]["allowed_actions"] == ["start"]
    assert len(before["startable_publication_ids"]) == 3

    response = await start(works)
    assert response.status_code == 202, response.text
    queued = response.json()
    assert queued["status"] == "queued"
    assert queued["allowed_actions"] == ["pause", "stop"]
    assert [work["execution"] for work in queued["works"]] == ["queued"] * 3

    await run_worker()
    found = await extraction(works)

    operation = found["operation"]
    assert operation["status"] == "completed"
    assert operation["progress"] == 1
    assert runs(found) == [(title, "done", "measures_extracted") for title in TITLES]
    assert [work["measure_count"] for work in operation["works"]] == [2, 2, 2]
    # Обработанные работы повторно не запускаются.
    assert found["startable_publication_ids"] == []
    assert operation["allowed_actions"] == []
    assert len(extractor.calls) == 3


async def test_same_key_returns_the_same_operation(works, run_worker):
    body = snapshot(await extraction(works))
    first = await start(works, "key-1", body)
    again = await start(works, "key-1", body)

    assert again.status_code == 202
    assert again.json() == first.json()
    assert (await extraction(works))["operation"]["status"] == "queued"
    # Одна операция: после обработки её итог — одна строка на работу.
    await run_worker()
    assert len((await extraction(works))["operation"]["works"]) == 3


async def test_same_key_with_other_input_is_a_conflict(works):
    found = await extraction(works)
    assert (await start(works, "key-1", snapshot(found))).status_code == 202

    other = snapshot(found, found["startable_publication_ids"][:1])
    response = await start(works, "key-1", other)

    assert response.status_code == 409
    assert response.json()["target"] == "idempotency_key"


async def test_new_key_while_the_slot_is_busy_is_refused(works):
    body = snapshot(await extraction(works))
    assert (await start(works, "key-1", body)).status_code == 202

    response = await start(works, "key-2", body)

    assert response.status_code == 409
    assert response.json()["message"].startswith("Идёт извлечение")


async def test_stale_document_versions_are_refused(works):
    found = await extraction(works)
    body = snapshot(found)
    body["document_versions"][0]["version"] += 1

    response = await start(works, body=body)

    assert response.status_code == 409
    assert response.json()["target"] == "document_versions"
    assert (await extraction(works))["operation"]["status"] == "not_started"


async def test_error_of_one_work_does_not_stop_the_others(works, run_worker, extractor):
    extractor.replies = [
        EXTRACTED,
        WorkResult("error", None, "Модель вернула ответ вне схемы."),
        WorkResult("no_measures", 0, None),
    ]
    await start(works)
    await run_worker()

    found = await extraction(works)
    assert found["operation"]["status"] == "completed"
    assert runs(found) == [
        (TITLES[0], "done", "measures_extracted"),
        (TITLES[1], "failed", "error"),
        (TITLES[2], "done", "no_measures"),
    ]
    assert found["operation"]["works"][1]["detail"] == "Модель вернула ответ вне схемы."
    assert found["operation"]["allowed_actions"] == ["retry_failed"]


async def test_unexpected_error_of_a_work_is_its_error(works, run_worker, extractor):
    extractor.replies = [RuntimeError("boom")]
    await start(works)
    await run_worker()

    found = await extraction(works)
    assert found["operation"]["status"] == "completed"
    assert [work["execution"] for work in found["operation"]["works"]] == [
        "failed",
        "done",
        "done",
    ]


async def test_retry_processes_only_failed_works(works, run_worker, extractor):
    extractor.replies = [EXTRACTED, WorkResult("error", None, "Сбой."), EXTRACTED]
    await start(works)
    await run_worker()
    failed = (await extraction(works))["operation"]["works"][1]["publication_id"]
    extractor.calls.clear()

    response = await works.post("/api/extraction/retry-failed")
    assert response.status_code == 200, response.text
    assert [work["execution"] for work in response.json()["works"]] == ["done", "queued", "done"]
    await run_worker()

    found = await extraction(works)
    assert [str(call) for call in extractor.calls] == [failed]
    assert runs(found) == [(title, "done", "measures_extracted") for title in TITLES]
    assert found["operation"]["allowed_actions"] == []
    assert (await works.post("/api/extraction/retry-failed")).status_code == 409


async def test_pause_is_confirmed_after_the_current_work(works, run_worker, extractor):
    paused: list[dict] = []

    async def pause():
        response = await works.post("/api/extraction/pause")
        paused.append(response.json())

    extractor.during = pause
    await start(works)
    await run_worker()

    # Запрос паузы не выдаётся за паузу: текущая работа ещё обрабатывается.
    assert paused[0]["status"] == "pause_requested"
    assert paused[0]["allowed_actions"] == ["stop"]
    found = await extraction(works)
    assert found["operation"]["status"] == "paused"
    assert found["operation"]["allowed_actions"] == ["resume", "stop"]
    assert [work["execution"] for work in found["operation"]["works"]] == [
        "done",
        "queued",
        "queued",
    ]
    # Операция на паузе занимает общий слот: документы не меняются.
    assert all(row["allowed_actions"] == [] for row in found["documents"])

    assert (await works.post("/api/extraction/resume")).json()["status"] == "queued"
    await run_worker()
    found = await extraction(works)
    assert found["operation"]["status"] == "completed"
    assert len(extractor.calls) == 3


async def test_queued_operation_pauses_at_once(works, run_worker, extractor):
    await start(works)

    assert (await works.post("/api/extraction/pause")).json()["status"] == "paused"
    await run_worker()
    assert extractor.calls == []


async def test_stop_is_confirmed_after_the_current_work(works, run_worker, extractor):
    stopping: list[dict] = []

    async def stop():
        stopping.append((await works.post("/api/extraction/stop")).json())

    extractor.during = stop
    await start(works)
    await run_worker()

    assert stopping[0]["status"] == "terminate_requested"
    assert stopping[0]["allowed_actions"] == []
    found = await extraction(works)
    assert found["operation"]["status"] == "terminated"
    assert runs(found) == [
        (TITLES[0], "done", "measures_extracted"),
        (TITLES[1], "skipped", None),
        (TITLES[2], "skipped", None),
    ]
    # Необработанные работы можно запустить снова; готовая не повторяется.
    skipped = [work["publication_id"] for work in found["operation"]["works"][1:]]
    assert found["startable_publication_ids"] == skipped
    assert found["operation"]["allowed_actions"] == ["start"]

    assert (await start(works, "key-2")).status_code == 202
    await run_worker()
    assert runs(await extraction(works)) == [
        (title, "done", "measures_extracted") for title in TITLES
    ]
    assert len(extractor.calls) == 3


async def test_paused_operation_stops_at_once(works, run_worker):
    await start(works)
    await works.post("/api/extraction/pause")

    stopped = (await works.post("/api/extraction/stop")).json()

    assert stopped["status"] == "terminated"
    assert {work["execution"] for work in stopped["works"]} == {"skipped"}
    assert stopped["allowed_actions"] == ["start"]


async def test_worker_crash_after_a_saved_work_continues_the_rest(works, run_worker, extractor, db):
    extractor.replies = [EXTRACTED, Crash()]
    await start(works)

    with pytest.raises(Crash):
        await run_worker()
    found = await extraction(works)
    assert found["operation"]["status"] == "running"
    assert [work["execution"] for work in found["operation"]["works"]] == [
        "done",
        "running",
        "queued",
    ]

    await worker.recover(db)
    await run_worker()

    found = await extraction(works)
    assert found["operation"]["status"] == "completed"
    # Сохранённая до сбоя работа не обрабатывается повторно.
    first, second, third = (int(work["publication_id"]) for work in found["operation"]["works"])
    assert extractor.calls == [first, second, second, third]
    assert runs(found) == [(title, "done", "measures_extracted") for title in TITLES]


async def test_stop_requested_before_a_crash_is_carried_out(works, run_worker, extractor, db):
    async def stop():
        await works.post("/api/extraction/stop")
        raise Crash

    extractor.during = stop
    await start(works)
    with pytest.raises(Crash):
        await run_worker()

    await worker.recover(db)
    await run_worker()

    found = await extraction(works)
    assert found["operation"]["status"] == "terminated"
    assert [work["execution"] for work in found["operation"]["works"]] == ["skipped"] * 3


async def test_unanswered_model_waits_for_explicit_resume(works, run_worker, extractor):
    extractor.replies = [EXTRACTED, ModelUnavailable("тайм-аут после 3 попыток.")]
    await start(works)
    await run_worker()

    found = await extraction(works)
    assert found["operation"]["status"] == "waiting_llm"
    assert found["operation"]["allowed_actions"] == ["resume", "stop"]
    assert [work["execution"] for work in found["operation"]["works"]] == [
        "done",
        "queued",
        "queued",
    ]

    await works.post("/api/extraction/resume")
    await run_worker()
    assert (await extraction(works))["operation"]["status"] == "completed"
    assert len(extractor.calls) == 4


async def test_log_records_commands_confirmations_and_outcomes(works, run_worker, extractor):
    async def pause():
        await works.post("/api/extraction/pause")

    extractor.during = pause
    extractor.replies = [EXTRACTED, WorkResult("error", None, "Сбой модели.")]
    await start(works)
    await run_worker()
    await works.post("/api/extraction/resume")
    await run_worker()

    response = await works.get("/api/extraction/log")
    assert response.status_code == 200
    messages = [event["message"] for event in response.json()]
    assert messages == [
        "Запуск извлечения: 3 работы.",
        "Запрошена пауза: текущая работа завершается.",
        f"Author 2020 · {TITLES[0]}: показатели извлечены.",
        "Пауза подтверждена.",
        "Продолжение.",
        f"Author 2020 · {TITLES[1]}: ошибка. Сбой модели.",
        f"Author 2020 · {TITLES[2]}: показатели извлечены.",
        # Завершение попытки называет итоги работ, а не выдаёт себя за успех.
        "Извлечение завершено для 3 работ: показатели извлечены — 2, ошибка — 1.",
    ]


async def test_log_does_not_double_the_title_period(confirmed, run_worker, sources):  # noqa: F811
    title = "Lifespan of captive animals."
    client = await prepared(confirmed, run_worker, sources, record(1, title=title))
    await start(client)
    await run_worker()

    messages = [event["message"] for event in (await client.get("/api/extraction/log")).json()]
    assert messages[1] == "Author 2020 · Lifespan of captive animals: показатели извлечены."
    assert messages[2] == "Извлечение завершено для 1 работы: показатели извлечены — 1."


async def test_not_connected_extractor_fails_works_honestly():
    result = await NotConnected().extract(WorkInput(1, "Title", "Question?", [], None, []))
    assert (result.outcome, result.detail) == (
        "error",
        "Подключение к модели не настроено: показатели не извлекаются.",
    )


async def test_replayed_key_returns_its_own_operation_after_a_later_one(
    works, run_worker, extractor
):
    extractor.replies = [EXTRACTED, WorkResult("error", None, "Сбой."), EXTRACTED]
    body = snapshot(await extraction(works))
    first = (await start(works, "key-1", body)).json()
    await run_worker()
    await works.post("/api/extraction/retry-failed")

    replayed = await start(works, "key-1", body)

    assert replayed.status_code == 202
    assert replayed.json()["status"] == "completed"
    assert len(replayed.json()["works"]) == len(first["works"])
    assert (await extraction(works))["operation"]["status"] == "queued"


async def test_pause_requested_during_a_model_wait_is_confirmed(works, run_worker, extractor):
    async def pause():
        await works.post("/api/extraction/pause")

    extractor.during = pause
    extractor.replies = [ModelUnavailable("тайм-аут.")]
    await start(works)
    await run_worker()

    found = await extraction(works)
    assert found["operation"]["status"] == "paused"
    assert [work["execution"] for work in found["operation"]["works"]] == ["queued"] * 3
