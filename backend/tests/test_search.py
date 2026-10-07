"""Поиск в PubMed и Europe PMC по версии критериев на HTTP API (#11, AC01–02).

Источники подменены сохранёнными ответами `data/corpus/search-responses`; worker
запускается в тесте явно, чтобы наблюдать запуск до и после ответа источников.
"""

import asyncio
import csv

import httpx
import pytest
from sqlalchemy import select

from evidence_compass import worker
from evidence_compass.criteria import CriteriaVersionRecord
from evidence_compass.search import SearchRecordRecord, SearchRunRecord, SearchSourceRecord
from tests.fake_sources import CORPUS
from tests.test_criteria import fields, prepared
from tests.test_question import NAKED_MOLE_RAT, account, sign_in  # noqa: F401

PUBMED = "eutils.ncbi.nlm.nih.gov"
EPMC = "www.ebi.ac.uk"


@pytest.fixture
async def confirmed(client, account, run_worker):  # noqa: F811
    """Вошедший пользователь с подтверждённой первой версией критериев."""
    await sign_in(client)
    await client.put("/api/question", json={"text": NAKED_MOLE_RAT})
    await prepared(client, run_worker)
    assert (await client.post("/api/criteria/confirm")).json() == {"version": 1}
    return client


async def reconfirm(client, change) -> None:
    draft = fields((await client.get("/api/criteria")).json())
    change(draft)
    assert (await client.put("/api/criteria", json=draft)).status_code == 200
    assert (await client.post("/api/criteria/confirm")).status_code == 200


async def searched(client, run_worker) -> dict:
    assert (await client.post("/api/search/start")).status_code == 202
    await run_worker()
    return (await client.get("/api/search")).json()


def outcomes(run: dict) -> list[tuple]:
    return [(s["resource"], s["status"], s["hit_count"], s["error"]) for s in run["sources"]]


async def test_search_waits_for_confirmed_criteria(client, account):  # noqa: F811
    await sign_in(client)

    assert (await client.get("/api/search")).status_code == 404
    assert (await client.post("/api/search/start")).status_code == 404


async def test_queries_are_shown_before_the_explicit_start(confirmed, sources):
    run = (await confirmed.get("/api/search")).json()

    assert run["status"] == "not_started"
    assert run["criteria_version"] == 1
    assert run["allowed_actions"] == ["start"]
    assert run["selection_blocked_reason"] == "Сначала запустите поиск."
    assert outcomes(run) == [
        ("pubmed", "pending", None, None),
        ("europe_pmc", "pending", None, None),
    ]
    # Список видов и критерий с теми же терминами — одна группа запроса.
    assert run["sources"][0]["query"] == (
        '("Heterocephalus glaber"[tiab] OR "Somniosus microcephalus"[tiab])'
        " AND (lifespan[tiab] OR longevity[tiab])"
        " AND (journal article[pt] OR preprint[Filter])"
    )
    assert run["sources"][1]["query"] == (
        '(TITLE_ABS:"Heterocephalus glaber" OR TITLE_ABS:"Somniosus microcephalus")'
        " AND (TITLE_ABS:lifespan OR TITLE_ABS:longevity)"
        " AND ((SRC:MED OR SRC:PMC) OR SRC:PPR)"
    )
    # Подтверждение не запускает поиск.
    assert sources.requests == []
    assert (await confirmed.get("/api/study")).json()["reached_step"] == "search"


async def test_every_confirmed_criterion_reaches_both_sources(confirmed, run_worker, sources):
    def narrow(draft: dict) -> None:
        draft["period"] = {"from_year": 2000, "to_year": None}
        draft["conditions"] = ["captive"]
        draft["publication_kinds"] = ["journal_article", "other"]
        draft["article_types"] = ["primary_study", "review"]

    await reconfirm(confirmed, narrow)
    shown = [s["query"] for s in (await confirmed.get("/api/search")).json()["sources"]]
    await searched(confirmed, run_worker)

    base = (
        '("Heterocephalus glaber"[tiab] OR "Somniosus microcephalus"[tiab])'
        " AND (lifespan[tiab] OR longevity[tiab])"
    )
    assert shown == [
        f"{base} AND (captive[tiab] OR captivity[tiab] OR zoo[tiab] OR laboratory[tiab])"
        " AND 2000:3000[dp]"
        " NOT preprint[Filter]"
        " NOT (meta-analysis[pt] OR (editorial[pt] OR letter[pt] OR comment[pt] OR news[pt]))",
        '(TITLE_ABS:"Heterocephalus glaber" OR TITLE_ABS:"Somniosus microcephalus")'
        " AND (TITLE_ABS:lifespan OR TITLE_ABS:longevity)"
        " AND (TITLE_ABS:captive OR TITLE_ABS:captivity OR TITLE_ABS:zoo OR TITLE_ABS:laboratory)"
        " AND PUB_YEAR:[2000 TO 3000]"
        " NOT SRC:PPR"
        ' NOT (PUB_TYPE:"meta-analysis" OR (PUB_TYPE:"editorial" OR PUB_TYPE:"letter"'
        ' OR PUB_TYPE:"comment" OR PUB_TYPE:"news"))',
    ]
    # Источникам уходит ровно показанный запрос: скрытого переноса критериев в отбор нет.
    assert sources.queries(PUBMED) + sources.queries(EPMC) == shown


async def test_search_runs_in_the_background_and_records_hits(confirmed, run_worker, db, llm):
    client = confirmed
    model_calls = len(llm.calls)

    started = await client.post("/api/search/start")
    busy = await client.post("/api/criteria/confirm")
    again = await client.post("/api/search/start")

    assert started.status_code == 202
    run = started.json()
    assert (run["status"], run["progress"], run["allowed_actions"]) == ("running", 0, ["stop"])
    assert run["selection_blocked_reason"] == "Дождитесь завершения поиска."
    # Поиск занимает общий слот: изменения ждут, чтение доступно.
    assert busy.status_code == 409
    assert again.status_code == 409 and "Идёт поиск" in again.json()["message"]
    assert (await client.get("/api/criteria")).status_code == 200

    assert await run_worker() == 1
    assert len(llm.calls) == model_calls
    run = (await client.get("/api/search")).json()

    assert run["status"] == "completed"
    assert run["progress"] == 1
    assert outcomes(run) == [("pubmed", "hits", 1, None), ("europe_pmc", "hits", 1, None)]
    assert [s["returned_count"] for s in run["sources"]] == [1, 1]
    assert run["allowed_actions"] == ["retry"]
    assert run["selection_blocked_reason"] is None
    assert (await client.get("/api/study")).json()["reached_step"] == "selection"

    records = (await db.scalars(select(SearchRecordRecord).order_by(SearchRecordRecord.id))).all()
    assert [(r.source_record_id, r.pmid, r.doi, r.year) for r in records] == [
        ("29364116", "29364116", "10.7554/elife.31157", 2018),
        ("MED:29364116", "29364116", "10.7554/elife.31157", 2018),
    ]
    pubmed, epmc = records
    title = "Naked Mole-Rat mortality rates defy gompertzian laws by not increasing with age."
    assert pubmed.title == epmc.title == title
    assert pubmed.authors == epmc.authors == "Ruby JG, Smith M, Buffenstein R"
    assert pubmed.abstract.startswith("The longest-lived rodent, the naked mole-rat")
    assert epmc.pmcid == "PMC5783610"


def corpus_checks() -> dict[str, dict]:
    """Ожидаемые PMID и DOI записей корпуса: проверки PubMed по DOI (`search-checks.csv`)."""
    with (CORPUS.parent / "search-checks.csv").open() as file:
        return {
            row["publication_id"]: row
            for row in csv.DictReader(file)
            if (row["source"], row["check_type"]) == ("pubmed", "known_item_api_lookup")
        }


@pytest.mark.parametrize("work", ["ruby2018", "nielsen2016", "podlutsky2005", "clubb2008"])
async def test_records_of_each_corpus_work_keep_their_identifiers(
    confirmed, run_worker, sources, db, work
):
    sources.pubmed = sources.europe_pmc = work
    expected = corpus_checks()[work]

    run = await searched(confirmed, run_worker)

    assert outcomes(run) == [("pubmed", "hits", 1, None), ("europe_pmc", "hits", 1, None)]
    records = (await db.scalars(select(SearchRecordRecord).order_by(SearchRecordRecord.id))).all()
    doi = expected["query"].removesuffix("[doi]").lower()
    assert [(r.pmid, r.doi) for r in records] == [(expected["expected_pmid"], doi)] * 2
    assert all(r.title and r.authors and r.year and r.abstract for r in records)


@pytest.mark.parametrize(
    ("pubmed", "europe_pmc", "expected", "blocked"),
    [
        (
            "empty",
            "empty",
            [("pubmed", "empty", 0, None), ("europe_pmc", "empty", 0, None)],
            None,
        ),
        (
            503,
            "podlutsky2005",
            [("pubmed", "error", None, "PubMed ответил 503."), ("europe_pmc", "hits", 1, None)],
            None,
        ),
        (
            httpx.ConnectTimeout("timed out"),
            503,
            [
                ("pubmed", "error", None, "PubMed не ответил за 30 с."),
                ("europe_pmc", "error", None, "Europe PMC ответил 503."),
            ],
            "Все ресурсы завершились ошибкой: повторите поиск или измените критерии.",
        ),
        (
            # Ошибка разбора запроса приходит с count 0 — это отказ, а не пустая выдача.
            httpx.Response(
                200, json={"esearchresult": {"count": "0", "idlist": [], "ERROR": "bad query"}}
            ),
            httpx.Response(200, text="<html>maintenance</html>"),
            [
                ("pubmed", "error", None, "PubMed отклонил запрос: bad query"),
                ("europe_pmc", "error", None, "Europe PMC вернул ответ неожиданного вида."),
            ],
            "Все ресурсы завершились ошибкой: повторите поиск или измените критерии.",
        ),
    ],
    ids=["empty", "partial", "all-failed", "malformed"],
)
async def test_empty_partial_and_failed_outcomes_differ(
    confirmed, run_worker, sources, pubmed, europe_pmc, expected, blocked
):
    sources.pubmed, sources.europe_pmc = pubmed, europe_pmc

    run = await searched(confirmed, run_worker)

    assert run["status"] == "completed"
    assert outcomes(run) == expected
    assert [s["returned_count"] for s in run["sources"]] == [
        0 if status == "empty" else 1 if status == "hits" else None for _, status, _, _ in expected
    ]
    assert run["selection_blocked_reason"] == blocked
    assert run["allowed_actions"] == ["retry"]
    step = (await confirmed.get("/api/study")).json()["reached_step"]
    assert step == ("search" if blocked else "selection")


async def test_only_chosen_resources_are_searched(confirmed, run_worker, sources):
    await reconfirm(confirmed, lambda draft: draft.update(resources=["europe_pmc"]))

    run = await searched(confirmed, run_worker)

    assert outcomes(run) == [("europe_pmc", "hits", 1, None)]
    assert sources.queries(PUBMED) == []


async def test_transient_failure_can_recover_in_the_same_run(confirmed, run_worker, sources):
    async def unavailable_once(request: httpx.Request) -> None:
        if request.url.host == EPMC:
            sources.europe_pmc = 503 if len(sources.queries(EPMC)) == 1 else "ruby2018"

    sources.during = unavailable_once

    run = await searched(confirmed, run_worker)

    assert outcomes(run) == [("pubmed", "hits", 1, None), ("europe_pmc", "hits", 1, None)]
    assert len(sources.queries(EPMC)) == 2
    assert len(sources.queries(PUBMED)) == 1


@pytest.mark.parametrize("code, attempts", [(503, 3), (429, 3), (400, 1)])
async def test_source_retry_budget_and_permanent_errors(
    confirmed, run_worker, sources, code, attempts
):
    sources.europe_pmc = code

    run = await searched(confirmed, run_worker)

    assert outcomes(run) == [
        ("pubmed", "hits", 1, None),
        ("europe_pmc", "error", None, f"Europe PMC ответил {code}."),
    ]
    assert len(sources.queries(EPMC)) == attempts
    assert run["selection_blocked_reason"] is None


async def test_repeated_search_is_a_new_run_of_its_criteria_version(
    confirmed, run_worker, sources, db
):
    client = confirmed
    sources.pubmed = 503
    first = await searched(client, run_worker)

    sources.pubmed = "ruby2018"
    retried = await searched(client, run_worker)

    def extend(draft: dict) -> None:
        draft["search_criteria"][1]["terms"].append("maximum longevity")
        draft["objects"][0]["aliases"] = ["naked mole-rat"]

    await reconfirm(client, extend)
    fresh = (await client.get("/api/search")).json()
    latest = await searched(client, run_worker)

    assert outcomes(first)[0][1] == "error"
    assert outcomes(retried)[0][1] == "hits"
    # Новая версия критериев ещё не искалась, пока поиск не запущен явно.
    assert (fresh["status"], fresh["criteria_version"]) == ("not_started", 2)
    assert (latest["status"], latest["criteria_version"]) == ("completed", 2)
    assert '"maximum longevity"' in latest["sources"][0]["query"]
    assert '"naked mole-rat"[tiab]' in latest["sources"][0]["query"]

    runs = (
        await db.execute(
            select(SearchRunRecord.number, CriteriaVersionRecord.number)
            .join(
                CriteriaVersionRecord,
                SearchRunRecord.criteria_version_id == CriteriaVersionRecord.id,
            )
            .order_by(SearchRunRecord.number)
        )
    ).all()
    assert runs == [(1, 1), (2, 1), (3, 2)]
    # Прежний запуск не изменился: его исходы и запросы остаются при своей версии.
    earliest = (
        await db.scalars(
            select(SearchSourceRecord)
            .join(SearchRunRecord)
            .where(SearchRunRecord.number == 1)
            .order_by(SearchSourceRecord.id)
        )
    ).all()
    assert [(s.status, s.error) for s in earliest] == [
        ("error", "PubMed ответил 503."),
        ("hits", None),
    ]
    assert "maximum longevity" not in earliest[0].query
    assert "naked mole-rat" not in earliest[0].query


async def test_queued_search_stops_at_once(confirmed, run_worker, sources):
    await confirmed.post("/api/search/start")

    stopped = (await confirmed.post("/api/search/stop")).json()

    assert stopped["status"] == "stopped"
    assert outcomes(stopped) == [
        ("pubmed", "stopped", None, None),
        ("europe_pmc", "stopped", None, None),
    ]
    assert stopped["allowed_actions"] == ["retry"]
    assert stopped["selection_blocked_reason"] == "Поиск остановлен: повторите поиск."
    assert await run_worker() == 0
    assert sources.requests == []


async def test_stop_during_a_request_keeps_the_received_outcome(confirmed, run_worker, sources):
    client = confirmed
    seen: list[dict] = []

    async def stop_once(request: httpx.Request) -> None:
        if not seen:
            seen.append((await client.post("/api/search/stop")).json())

    sources.during = stop_once
    await client.post("/api/search/start")
    await run_worker()
    run = (await client.get("/api/search")).json()

    # Запрос уже в пути: остановка лишь запрошена и больше не предлагается.
    assert seen[0]["status"] == "running"
    assert seen[0]["allowed_actions"] == []
    assert run["status"] == "stopped"
    assert outcomes(run) == [("pubmed", "hits", 1, None), ("europe_pmc", "stopped", None, None)]
    assert [s["returned_count"] for s in run["sources"]] == [1, None]
    assert sources.queries(EPMC) == []
    assert run["allowed_actions"] == ["retry"]
    assert (await client.get("/api/study")).json()["reached_step"] == "search"


async def test_worker_restart_continues_only_unanswered_sources(confirmed, run_worker, sources, db):
    async def crash_on_europe_pmc(request: httpx.Request) -> None:
        if request.url.host == EPMC:
            sources.during = None
            # Процесс worker прерван во время запроса.
            raise asyncio.CancelledError

    sources.during = crash_on_europe_pmc
    await confirmed.post("/api/search/start")
    with pytest.raises(asyncio.CancelledError):
        await run_worker()
    interrupted = (await confirmed.get("/api/search")).json()

    await worker.recover(db)
    assert await run_worker() == 1
    run = (await confirmed.get("/api/search")).json()

    assert outcomes(interrupted) == [
        ("pubmed", "hits", 1, None),
        ("europe_pmc", "running", None, None),
    ]
    assert run["status"] == "completed"
    assert outcomes(run) == [("pubmed", "hits", 1, None), ("europe_pmc", "hits", 1, None)]
    # PubMed не опрашивается повторно, его записи не дублируются.
    assert len(sources.queries(PUBMED)) == 1
    assert len((await db.scalars(select(SearchRecordRecord))).all()) == 2


async def test_unexpected_worker_error_frees_the_slot(confirmed, run_worker, sources):
    async def broken(request: httpx.Request) -> None:
        raise RuntimeError("boom")

    sources.during = broken
    await confirmed.post("/api/search/start")
    with pytest.raises(RuntimeError):
        await run_worker()
    run = (await confirmed.get("/api/search")).json()

    assert run["status"] == "completed"
    assert {s["error"] for s in run["sources"]} == {"внутренняя ошибка обработки"}
    assert run["allowed_actions"] == ["retry"]


async def test_other_operation_holds_the_slot(confirmed):
    assert (await confirmed.post("/api/question/prepare-criteria")).status_code == 202

    run = (await confirmed.get("/api/search")).json()
    refused = await confirmed.post("/api/search/start")

    assert run["allowed_actions"] == []
    assert refused.status_code == 409
    assert "подготовка критериев" in refused.json()["message"]


async def test_counts_show_saved_records_not_the_source_total(confirmed, run_worker, sources, db):
    from tests.fake_sources import _json

    payload = _json("ruby2018-epmc.json")
    item = payload["resultList"]["result"][0]
    payload["hitCount"] = 630
    payload["resultList"]["result"] = [dict(item, id=str(i)) for i in range(200)]
    sources.europe_pmc = httpx.Response(200, json=payload)
    before = (await confirmed.get("/api/search")).json()
    assert [s["returned_count"] for s in before["sources"]] == [None, None]

    run = await searched(confirmed, run_worker)

    assert [(s["hit_count"], s["returned_count"]) for s in run["sources"]] == [(1, 1), (630, 200)]
    assert len((await db.scalars(select(SearchRecordRecord))).all()) == 201
    assert run["selection_blocked_reason"] is None


async def test_oversized_criteria_stay_saved_but_cannot_be_confirmed(confirmed, sources):
    draft = fields((await confirmed.get("/api/criteria")).json())
    draft["search_criteria"][0]["terms"] = ["x" * 2001]
    saved = await confirmed.put("/api/criteria", json=draft)
    assert saved.status_code == 200
    assert "confirm" not in saved.json()["allowed_actions"]
    assert {"PubMed", "Europe PMC"} == {
        name
        for name in ("PubMed", "Europe PMC")
        if any(name in p["message"] for p in saved.json()["problems"])
    }
    assert (await confirmed.post("/api/criteria/confirm")).status_code == 409
    assert (await confirmed.get("/api/criteria")).json()["search_criteria"][0]["terms"] == [
        "x" * 2001
    ]
    assert sources.requests == []


async def test_old_invalid_version_is_readable_but_start_is_refused(confirmed, db, sources):
    version = await db.scalar(select(CriteriaVersionRecord))
    content = dict(version.fields)
    content["search_criteria"] = [dict(content["search_criteria"][0], terms=["x" * 2001])]
    version.fields = content
    await db.commit()

    assert (await confirmed.get("/api/search")).status_code == 200
    refused = await confirmed.post("/api/search/start")
    assert refused.status_code == 409
    assert "2000" in refused.json()["message"]
    assert await db.scalar(select(SearchRunRecord)) is None
    assert sources.requests == []


async def test_queued_search_executes_its_saved_queries(confirmed, run_worker, sources, db):
    await confirmed.post("/api/search/start")
    rows = (await db.scalars(select(SearchSourceRecord).order_by(SearchSourceRecord.id))).all()
    for row in rows:
        row.query = "longevity"  # A query saved by an earlier renderer.
    await db.commit()

    await run_worker()

    assert sources.queries(PUBMED) == sources.queries(EPMC) == ["longevity"]
    assert [s["query"] for s in (await confirmed.get("/api/search")).json()["sources"]] == [
        "longevity"
    ] * 2
