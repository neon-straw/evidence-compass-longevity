"""Отбор публикаций на HTTP API (#12, AC03).

Решение о соответствии критериям хранит автора, причину и версию критериев. Найденная запись
ждёт решения пользователя; выбор такой записи для извлечения включает её от имени
пользователя. Препринт и журнальная версия не сливаются без явного подтверждения.
"""

import httpx
import pytest

from tests.test_question import account  # noqa: F401
from tests.test_search import EPMC, confirmed, reconfirm, searched  # noqa: F401

RUBY = "Naked Mole-Rat mortality rates defy gompertzian laws by not increasing with age."


def epmc(*records: dict) -> httpx.Response:
    """Выдача Europe PMC из заданных записей (формат `resultType=core`)."""
    return httpx.Response(
        200, json={"hitCount": len(records), "resultList": {"result": list(records)}}
    )


def record(number: int, **fields) -> dict:
    return {
        "source": "MED",
        "id": str(40000000 + number),
        "pmid": str(40000000 + number),
        "doi": f"10.1234/work.{number}",
        "title": f"Lifespan of captive animals, cohort {number}",
        "authorString": "Author A.",
        "pubYear": "2020",
        **fields,
    }


PREPRINT = {
    "source": "PPR",
    "id": "PPR123456",
    "doi": "10.1101/2017.12.01.000001",
    "title": "Naked mole-rat mortality rates defy Gompertzian laws by not increasing with age",
    "authorString": "Ruby JG, Smith M, Buffenstein R",
    "pubYear": "2017",
    "pubTypeList": {"pubType": ["Preprint"]},
}


async def selection(client) -> dict:
    response = await client.get("/api/selection")
    assert response.status_code == 200, response.text
    return response.json()


def row(found: dict, title: str) -> dict:
    return next(item for item in found["publications"] if item["title"] == title)


async def card(client, publication: dict) -> dict:
    response = await client.get(f"/api/publications/{publication['id']}")
    assert response.status_code == 200, response.text
    return response.json()


async def decide(client, publication: dict, decision: str, reason: str) -> httpx.Response:
    return await client.put(
        f"/api/publications/{publication['id']}/decision",
        json={"decision": decision, "reason": reason},
    )


async def select(client, publication: dict, selected: bool = True) -> httpx.Response:
    return await client.put(
        f"/api/publications/{publication['id']}/selected", json={"selected": selected}
    )


@pytest.fixture
async def searched_client(confirmed, run_worker):  # noqa: F811
    """Поиск по версии 1 нашёл ruby2018 в обоих ресурсах."""
    await searched(confirmed, run_worker)
    return confirmed


async def test_selection_waits_for_the_search(client, confirmed):  # noqa: F811
    found = await selection(confirmed)

    assert found["publications"] == []
    assert found["proceed_blocked_reason"] == "Сначала запустите поиск."


async def test_found_record_awaits_the_users_decision(searched_client):
    client = searched_client

    found = await selection(client)

    # Одна работа из PubMed и Europe PMC — одна запись с двумя источниками.
    assert len(found["publications"]) == 1
    ruby = found["publications"][0]
    assert ruby["title"] == RUBY
    assert ruby["doi"] == "10.7554/elife.31157"
    assert ruby["provenance"] == ["pubmed", "europe_pmc"]
    assert ruby["version_type"] == "journal"
    assert (ruby["decision"], ruby["selected"], ruby["select_blocked_reason"]) == (
        "needs_review",
        False,
        None,
    )
    assert (found["selected_count"], found["limit"]) == (0, 8)
    assert found["proceed_blocked_reason"] == "Выберите от одной до восьми работ."
    opened = await card(client, ruby)
    assert opened["decision_record"] == {
        "decision": "needs_review",
        "reason": "Найдена поиском по версии критериев 1; соответствие критериям не проверено.",
        "author": "system",
        "criteria_version": 1,
    }
    assert {(s["resource"], s["identifier"]) for s in opened["sources"]} == {
        ("pubmed", "29364116"),
        ("europe_pmc", "MED:29364116"),
    }
    assert opened["abstract"]


async def test_decision_keeps_author_reason_and_criteria_version(searched_client):
    client = searched_client
    ruby = (await selection(client))["publications"][0]

    response = await decide(client, ruby, "excluded", "Нет показателей продолжительности жизни.")

    assert response.status_code == 200, response.text
    assert response.json()["decision_record"] == {
        "decision": "excluded",
        "reason": "Нет показателей продолжительности жизни.",
        "author": "user",
        "criteria_version": 1,
    }
    excluded = (await selection(client))["publications"][0]
    assert excluded["select_blocked_reason"] == (
        "Исключённую работу выбрать нельзя: сначала измените решение."
    )
    assert (await select(client, excluded)).status_code == 409
    assert (await decide(client, ruby, "included", "  ")).status_code == 422


async def test_selecting_an_undecided_work_includes_it(searched_client):
    client = searched_client
    ruby = (await selection(client))["publications"][0]

    response = await select(client, ruby)

    assert response.status_code == 200, response.text
    assert response.json()["selected_count"] == 1
    assert response.json()["proceed_blocked_reason"] is None
    assert (await card(client, ruby))["decision_record"] == {
        "decision": "included",
        "reason": "Отмечена соответствующей пользователем.",
        "author": "user",
        "criteria_version": 1,
    }
    # Работа, которая больше не включена, не остаётся выбранной.
    await decide(client, ruby, "needs_review", "Условия наблюдения неясны.")
    assert (await selection(client))["selected_count"] == 0


async def test_ninth_selection_is_blocked_and_every_record_stays_visible(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    sources.pubmed = "empty"
    sources.europe_pmc = epmc(*(record(number) for number in range(10)))
    await searched(confirmed, run_worker)
    rows = (await selection(confirmed))["publications"]
    assert len(rows) == 10

    for publication in rows[:8]:
        assert (await select(confirmed, publication)).status_code == 200
    ninth = await select(confirmed, rows[8])

    assert ninth.status_code == 409
    assert ninth.json() == {
        "target": rows[8]["id"],
        "message": "Выбрано 8 из 8: снимите выбор с другой работы.",
    }
    found = await selection(confirmed)
    assert len(found["publications"]) == 10
    assert found["selected_count"] == 8
    assert [item["select_blocked_reason"] is None for item in found["publications"]] == [
        True
    ] * 8 + [False] * 2
    # Снятие выбора освобождает место.
    assert (await select(confirmed, rows[0], selected=False)).status_code == 200
    assert (await select(confirmed, rows[8])).status_code == 200


async def test_preprint_and_journal_version_merge_only_after_confirmation(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    sources.europe_pmc = epmc(PREPRINT)
    await searched(confirmed, run_worker)
    found = await selection(confirmed)
    journal, preprint = row(found, RUBY), row(found, PREPRINT["title"])

    # Похожие названия дают лишь предложение связи, а не объединение.
    assert len(found["publications"]) == 2
    assert preprint["version_type"] == "preprint"
    assert (journal["version_link"], preprint["version_link"]) == ("needs_review", "needs_review")
    assert (await card(confirmed, journal))["related_versions"] == [
        {
            "id": preprint["id"],
            "title": PREPRINT["title"],
            "version_type": "preprint",
            "link": "needs_review",
        }
    ]

    linked = await confirmed.post(
        f"/api/publications/{preprint['id']}/version-links",
        json={"related_id": journal["id"], "same_work": True},
    )

    assert linked.status_code == 200, linked.text
    assert linked.json()["version_link"] == "confirmed"
    assert linked.json()["versions"] == 2
    assert row(await selection(confirmed), RUBY)["version_link"] == "confirmed"
    # Одна работа не выбирается дважды в разных версиях.
    assert (await select(confirmed, journal)).status_code == 200
    blocked = row(await selection(confirmed), PREPRINT["title"])["select_blocked_reason"]
    assert blocked == "Уже выбрана другая версия этой работы."

    separated = await confirmed.post(
        f"/api/publications/{journal['id']}/version-links",
        json={"related_id": preprint["id"], "same_work": False},
    )

    assert separated.json()["version_link"] == "none"
    assert separated.json()["related_versions"] == []
    assert row(await selection(confirmed), PREPRINT["title"])["select_blocked_reason"] is None


async def test_manual_addition_keeps_its_label(searched_client, run_worker, sources):
    client = searched_client
    sources.europe_pmc = "nielsen2016"

    candidates = await client.post(
        "/api/publications/lookup", json={"query": "https://doi.org/10.1126/SCIENCE.AAF1703"}
    )

    assert candidates.status_code == 200, candidates.text
    assert candidates.json() == [
        {
            "title": "Eye lens radiocarbon reveals centuries of longevity in the Greenland shark"
            " (Somniosus microcephalus).",
            "year": 2016,
            "doi": "10.1126/science.aaf1703",
            "pmid": "27516602",
            "already_listed": False,
        }
    ]
    assert sources.queries(EPMC)[-1] == 'DOI:"10.1126/science.aaf1703"'

    added = await client.post(
        "/api/publications/manual", json={"doi": "10.1126/science.aaf1703", "pmid": None}
    )

    assert added.status_code == 200, added.text
    assert added.json()["provenance"] == ["manual"]
    assert added.json()["decision"] == "needs_review"
    assert (await card(client, added.json()))["decision_record"]["author"] == "user"
    again = await client.post("/api/publications/lookup", json={"query": "27516602"})
    assert again.json()[0]["already_listed"] is True
    assert sources.queries(EPMC)[-1] == "EXT_ID:27516602 AND SRC:MED"
    duplicate = await client.post(
        "/api/publications/manual", json={"doi": None, "pmid": "27516602"}
    )
    assert duplicate.status_code == 409

    # Найденная позже поиском работа сохраняет метку ручного добавления.
    sources.pubmed = "nielsen2016"
    await searched(client, run_worker)
    shark = row(await selection(client), added.json()["title"])
    assert shark["provenance"] == ["manual", "pubmed", "europe_pmc"]


async def test_lookup_by_title_and_unknown_record(searched_client, sources):
    client = searched_client

    found = await client.post("/api/publications/lookup", json={"query": "mole-rat mortality"})

    assert found.json()[0]["already_listed"] is True
    assert sources.queries(EPMC)[-1] == 'TITLE:"mole-rat mortality"'
    sources.europe_pmc = "empty"
    missing = await client.post("/api/publications/manual", json={"doi": "10.1/none", "pmid": None})
    assert missing.status_code == 404


async def test_changed_criteria_ask_to_recheck_user_decisions(searched_client, run_worker):
    client = searched_client
    ruby = (await selection(client))["publications"][0]
    await decide(client, ruby, "included", "Показатели для колонии.")

    # Повтор поиска той же версии сохраняет решение.
    await searched(client, run_worker)
    assert (await selection(client))["publications"][0]["decision"] == "included"

    await reconfirm(client, lambda draft: draft.update(conditions=["captive"]))
    await searched(client, run_worker)

    assert (await card(client, ruby))["decision_record"] == {
        "decision": "needs_review",
        "reason": "Критерии изменились (версия 1 → 2): прежнее решение «Соответствует: да»"
        " требует повторной проверки.",
        "author": "system",
        "criteria_version": 2,
    }


async def test_changes_wait_for_the_search_slot(searched_client):
    client = searched_client
    ruby = (await selection(client))["publications"][0]
    assert (await client.post("/api/search/start")).status_code == 202

    refused = await select(client, ruby)

    assert refused.status_code == 409
    assert "Идёт поиск" in refused.json()["message"]
    assert (await decide(client, ruby, "excluded", "нет")).status_code == 409
    # Чтение сохранённого отбора доступно.
    assert (await selection(client))["publications"][0]["id"] == ruby["id"]


async def test_proceeding_to_documents_needs_a_selected_work(searched_client):
    client = searched_client

    refused = await client.post("/api/selection/proceed")

    assert refused.status_code == 409
    assert refused.json()["message"] == "Выберите от одной до восьми работ."
    await select(client, (await selection(client))["publications"][0])
    proceeded = await client.post("/api/selection/proceed")
    assert proceeded.json() == {"reached_step": "preparation"}
    assert (await client.get("/api/study")).json()["reached_step"] == "preparation"


async def test_unknown_publication(searched_client):
    assert (await searched_client.get("/api/publications/999")).status_code == 404
    assert (await searched_client.get("/api/publications/abc")).status_code == 404


async def test_conflicting_identifiers_do_not_break_the_search(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    sources.pubmed = "empty"
    sources.europe_pmc = epmc(record(1, pmid=None, id="PMC1", source="PMC"), record(2))
    await searched(confirmed, run_worker)
    # DOI указывает на первую запись, PMID — на вторую: записи не сливаются и не падают.
    sources.europe_pmc = epmc(record(1, pmid=record(2)["pmid"]))

    run = await searched(confirmed, run_worker)

    assert run["status"] == "completed"
    assert (await selection(confirmed))["publications"][0]["doi"] == "10.1234/work.1"


async def test_records_with_user_decisions_stay_visible_after_a_new_search(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    sources.pubmed = "empty"
    sources.europe_pmc = epmc(record(1), record(2))
    await searched(confirmed, run_worker)
    first, second = (await selection(confirmed))["publications"]
    await select(confirmed, first)
    await decide(confirmed, second, "excluded", "Не по теме.")

    sources.europe_pmc = epmc(record(3))
    await reconfirm(confirmed, lambda draft: draft.update(conditions=["captive"]))
    await searched(confirmed, run_worker)

    found = await selection(confirmed)
    assert [item["id"] for item in found["publications"]][:2] == [first["id"], second["id"]]
    # Прежнее решение не переносится как подтверждённое: выбор снят с объяснением.
    assert (await card(confirmed, first))["decision_record"]["reason"].startswith(
        "Критерии изменились (версия 1 → 2)"
    )
    assert found["selected_count"] == 0


async def test_manual_record_is_rechecked_after_criteria_change(
    searched_client, run_worker, sources
):
    client = searched_client
    sources.europe_pmc = "nielsen2016"
    added = (
        await client.post(
            "/api/publications/manual", json={"doi": "10.1126/science.aaf1703", "pmid": None}
        )
    ).json()
    assert (await select(client, added)).status_code == 200

    sources.europe_pmc = "ruby2018"
    await reconfirm(client, lambda draft: draft.update(conditions=["captive"]))
    await searched(client, run_worker)

    shark = row(await selection(client), added["title"])
    assert (shark["decision"], shark["selected"]) == ("needs_review", False)


async def test_journal_version_stays_selected_when_versions_are_linked(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    sources.europe_pmc = epmc(PREPRINT)
    await searched(confirmed, run_worker)
    found = await selection(confirmed)
    journal, preprint = row(found, RUBY), row(found, PREPRINT["title"])
    await select(confirmed, journal)
    await select(confirmed, preprint)

    await confirmed.post(
        f"/api/publications/{preprint['id']}/version-links",
        json={"related_id": journal["id"], "same_work": True},
    )

    found = await selection(confirmed)
    assert (row(found, RUBY)["selected"], row(found, PREPRINT["title"])["selected"]) == (
        True,
        False,
    )


async def test_publication_outside_the_selection_cannot_be_changed(
    searched_client, run_worker, sources
):
    client = searched_client
    ruby = (await selection(client))["publications"][0]
    sources.pubmed = sources.europe_pmc = "nielsen2016"
    await reconfirm(client, lambda draft: draft.update(conditions=["captive"]))
    await searched(client, run_worker)

    assert (await select(client, ruby)).status_code == 404
    assert (await card(client, ruby))["title"] == RUBY


@pytest.mark.parametrize(
    "conflict", [{"pmid": "39046104"}, {"doi": "10.2/other"}, {"pmcid": "PMC2"}]
)
async def test_shared_identifier_never_overrides_a_conflicting_one(
    confirmed,  # noqa: F811
    run_worker,
    sources,
    conflict,
):
    first = record(1, pmcid="PMC1")
    sources.pubmed = "empty"
    sources.europe_pmc = epmc(first, {**first, "id": "other", **conflict})

    await searched(confirmed, run_worker)

    assert len((await selection(confirmed))["publications"]) == 2


async def test_pmid_only_and_doi_only_records_merge_on_exact_title_and_year(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    sources.pubmed = "empty"
    sources.europe_pmc = epmc(
        record(1, doi=None),
        record(
            1, source="PMC", id="PMC9", pmid=None, title="Lifespan of  CAPTIVE animals, cohort 1"
        ),
        # Другой год — не та же запись.
        record(1, source="PMC", id="PMC8", pmid=None, doi="10.1234/other", pubYear="2019"),
    )

    await searched(confirmed, run_worker)

    rows = (await selection(confirmed))["publications"]
    assert len(rows) == 2
    assert (rows[0]["doi"], len((await card(confirmed, rows[0]))["sources"])) == (
        "10.1234/work.1",
        2,
    )


async def test_split_identifiers_never_merge_a_preprint_into_a_journal_version(
    confirmed,  # noqa: F811
    run_worker,
    sources,
):
    sources.pubmed = "empty"
    sources.europe_pmc = epmc(
        record(1, doi=None),
        record(1, source="PPR", id="PPR1", pmid=None, pubTypeList={"pubType": ["Preprint"]}),
    )

    await searched(confirmed, run_worker)

    rows = (await selection(confirmed))["publications"]
    assert [item["version_type"] for item in rows] == ["journal", "preprint"]
    # Одинаковое название даёт лишь предложение связи версий.
    assert [item["version_link"] for item in rows] == ["needs_review", "needs_review"]


async def test_internal_failure_after_sources_does_not_open_selection(
    confirmed,  # noqa: F811
    run_worker,
    monkeypatch,
):
    async def broken(db, run):
        raise RuntimeError("сбой переноса выдачи")

    monkeypatch.setattr("evidence_compass.selection.absorb", broken)
    assert (await confirmed.post("/api/search/start")).status_code == 202
    with pytest.raises(RuntimeError):
        await run_worker()

    run = (await confirmed.get("/api/search")).json()

    # Исходы ресурсов получены, но запуск не завершён: переход к отбору не предлагается.
    assert run["allowed_actions"] == ["retry"]
    assert run["selection_blocked_reason"] == ("Поиск прерван внутренней ошибкой: повторите поиск.")
    assert (await confirmed.get("/api/study")).json()["reached_step"] == "search"


@pytest.mark.parametrize(
    "pubmed,europe_pmc,expected",
    [
        ("ruby2018", "ruby2018", "open"),  # Europe PMC: isOpenAccess=Y, PDF в PMC
        ("nielsen2016", "nielsen2016", "closed"),  # только ссылка по DOI по подписке
        ("ruby2018", "empty", "unknown"),  # PubMed не сообщает об открытом тексте
    ],
)
async def test_open_text_is_reported_by_the_source_not_checked(
    confirmed,  # noqa: F811
    run_worker,
    sources,
    pubmed,
    europe_pmc,
    expected,
):
    sources.pubmed, sources.europe_pmc = pubmed, europe_pmc

    await searched(confirmed, run_worker)

    publication = (await selection(confirmed))["publications"][0]
    assert publication["open_access"] == expected
    assert "full_text" not in publication
