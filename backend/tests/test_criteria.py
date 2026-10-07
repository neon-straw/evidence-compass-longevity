"""Подготовка и подтверждение критериев на HTTP API с подменённой моделью (#10, AC01).

Worker запускается в тесте явно: между запуском подготовки и ответом модели
приложение наблюдается в состоянии «операция идёт».
"""

import pytest
from sqlalchemy import select

from evidence_compass.operations import OperationRecord
from tests.fake_llm import ACTUAL_MODEL, ACTUAL_PROVIDER, PROPOSAL, unavailable
from tests.test_question import NAKED_MOLE_RAT, TUATARA, account, sign_in  # noqa: F401

EDITABLE = (
    "objects",
    "period",
    "article_types",
    "publication_kinds",
    "conditions",
    "search_criteria",
    "resources",
)


def fields(draft: dict) -> dict:
    return {key: draft[key] for key in EDITABLE}


@pytest.fixture
async def signed_in(client, account):  # noqa: F811
    await sign_in(client)
    await client.put("/api/question", json={"text": NAKED_MOLE_RAT})
    return client


async def prepared(client, run_worker) -> dict:
    assert (await client.post("/api/question/prepare-criteria")).status_code == 202
    await run_worker()
    return (await client.get("/api/criteria")).json()


async def test_model_proposals_arrive_through_the_worker(signed_in, run_worker, llm, db):
    client = signed_in

    started = await client.post("/api/question/prepare-criteria")
    waiting = (await client.get("/api/criteria")).json()

    assert started.status_code == 202
    assert waiting["status"] == "preparing"
    assert waiting["allowed_actions"] == []
    assert llm.calls == []

    assert await run_worker() == 1
    draft = (await client.get("/api/criteria")).json()

    assert NAKED_MOLE_RAT in llm.calls[0].user
    assert draft["status"] == "ready"
    assert draft["gaps"] == [] and draft["problems"] == []
    assert [(o["name"], o["scientific_name"], o["origin"]) for o in draft["objects"]] == [
        (o["name"], o["scientific_name"], "suggested") for o in PROPOSAL["objects"]
    ]
    criteria = draft["search_criteria"]
    assert [(c["label"], c["terms"]) for c in criteria] == [
        (c["label"], c["terms"]) for c in PROPOSAL["search_criteria"]
    ]
    assert all(c["suggested_terms"] == c["terms"] and c["origin"] == "suggested" for c in criteria)
    ids = [item["id"] for item in draft["objects"] + criteria]
    assert len(set(ids)) == len(ids)
    # По умолчанию период и условия не ограничены, препринты включены (spec, п. 4).
    assert draft["period"] == {"from_year": None, "to_year": None}
    assert draft["conditions"] == ["wild", "captive"]
    assert draft["publication_kinds"] == ["journal_article", "preprint"]
    assert draft["resources"] == ["pubmed", "europe_pmc"]
    assert draft["confirmed_version"] is None
    assert draft["has_unconfirmed_changes"] is True
    assert draft["allowed_actions"] == ["confirm", "reformulate"]
    assert draft["saved_at"]

    operation = await db.scalar(select(OperationRecord))
    assert operation.status == "completed"
    # В операции — модель и провайдер из ответа, а не из настроек.
    assert (operation.model, operation.provider) == (ACTUAL_MODEL, ACTUAL_PROVIDER)


async def test_changes_wait_while_the_operation_holds_the_slot(signed_in, run_worker):
    client = signed_in
    await client.post("/api/question/prepare-criteria")
    draft = (await client.get("/api/criteria")).json()

    again = await client.post("/api/question/prepare-criteria")
    question = await client.put("/api/question", json={"text": TUATARA})
    criteria = await client.put("/api/criteria", json=fields(draft))
    confirm = await client.post("/api/criteria/confirm")
    reformulate = await client.post("/api/criteria/reformulate")
    manual = await client.post("/api/criteria/start-manually")

    for refused in (again, question, criteria, reformulate, manual):
        assert refused.status_code == 409, refused.text
        assert "подготовка критериев" in refused.json()["message"]
    assert confirm.status_code == 409
    # Чтение сохранённого разрешено во время операции.
    assert (await client.get("/api/question")).json()["text"] == NAKED_MOLE_RAT
    assert len((await client.get("/api/question/versions")).json()) == 1

    await run_worker()
    assert (await client.put("/api/question", json={"text": TUATARA})).status_code == 200


async def test_partial_proposal_names_the_gap_and_blocks_confirmation(signed_in, run_worker, llm):
    llm.default = {
        **PROPOSAL,
        "objects": [{"name": "Ночница Брандта", "scientific_name": None, "aliases": []}],
    }

    draft = await prepared(signed_in, run_worker)
    blocked = await signed_in.post("/api/criteria/confirm")

    assert draft["status"] == "partial"
    assert draft["gaps"] == [
        "Не получено научное название для «Ночница Брандта» — укажите его вручную."
    ]
    object_id = draft["objects"][0]["id"]
    assert [p["target"] for p in draft["problems"]] == [object_id]
    assert draft["allowed_actions"] == ["reformulate"]
    assert blocked.status_code == 409
    assert blocked.json()["problems"] == draft["problems"]

    draft["objects"][0].update(scientific_name="Myotis brandtii", origin="edited")
    fixed = (await signed_in.put("/api/criteria", json=fields(draft))).json()
    assert fixed["problems"] == []
    assert (await signed_in.post("/api/criteria/confirm")).json() == {"version": 1}


async def test_bounded_retries_then_failure_frees_the_slot(signed_in, run_worker, llm, db):
    llm.replies = [unavailable(), unavailable(), unavailable()]

    draft = await prepared(signed_in, run_worker)

    assert len(llm.calls) == 3
    assert draft["status"] == "failed"
    assert "OpenRouter ответил 502" in draft["gaps"][0]
    operation = await db.scalar(select(OperationRecord))
    assert (operation.status, operation.attempts) == ("failed", 3)
    # Модель не менялась, провайдер не ответил: фиксировать нечего.
    assert operation.model == "test/model" and operation.provider is None

    # Слот свободен: критерии можно заполнить вручную и подтвердить.
    manual = {
        **fields(draft),
        "objects": [
            {
                "id": "obj-new-1",
                "name": "Голый землекоп",
                "scientific_name": "Heterocephalus glaber",
                "origin": "added",
            }
        ],
    }
    assert (await signed_in.put("/api/criteria", json=manual)).status_code == 200
    assert (await signed_in.post("/api/criteria/confirm")).json() == {"version": 1}


async def test_a_retry_that_succeeds_is_not_a_failure(signed_in, run_worker, llm, db):
    llm.replies = [unavailable()]

    draft = await prepared(signed_in, run_worker)

    assert draft["status"] == "ready"
    assert (await db.scalar(select(OperationRecord))).attempts == 2


@pytest.mark.parametrize(
    ("change", "target", "reason"),
    [
        (lambda d: d.update(resources=[]), "resources", "ресурс"),
        (lambda d: d["search_criteria"][1].update(terms=[]), "criterion", "термин"),
        (
            lambda d: d["search_criteria"][1].update(terms=['"lifespan']),
            "criterion",
            "синтаксис",
        ),
        (
            lambda d: d["search_criteria"][1].update(terms=["lifespan[tiab]"]),
            "criterion",
            "синтаксис",
        ),
        (
            lambda d: d["search_criteria"][1].update(terms=["lifespan AND mortality"]),
            "criterion",
            "синтаксис",
        ),
        (
            lambda d: d.update(period={"from_year": 2020, "to_year": 2000}),
            "period",
            "период",
        ),
        (
            lambda d: d.update(objects=[], search_criteria=[]),
            "search_criteria",
            "пуст",
        ),
        # Пустой выбор из словаря не представим в запросе источника.
        (lambda d: d.update(conditions=[]), "conditions", "условие"),
        (lambda d: d.update(article_types=[]), "article_types", "тип статьи"),
        (lambda d: d.update(publication_kinds=[]), "publication_kinds", "вид публикации"),
    ],
)
async def test_unrepresentable_criteria_block_confirmation(
    signed_in, run_worker, change, target, reason
):
    draft = fields(await prepared(signed_in, run_worker))
    change(draft)
    if target == "criterion":
        target = draft["search_criteria"][1]["id"]

    saved = (await signed_in.put("/api/criteria", json=draft)).json()
    blocked = await signed_in.post("/api/criteria/confirm")

    assert [p["target"] for p in saved["problems"]] == [target]
    assert reason in saved["problems"][0]["message"].lower()
    assert "confirm" not in saved["allowed_actions"]
    assert blocked.status_code == 409
    assert blocked.json()["confirmed_version"] is None
    assert (await signed_in.get("/api/study")).json()["reached_step"] == "criteria"


async def test_confirmation_freezes_versions_of_the_criteria(signed_in, run_worker):
    client = signed_in
    draft = fields(await prepared(client, run_worker))

    first = await client.post("/api/criteria/confirm")
    after = (await client.get("/api/criteria")).json()

    assert first.json() == {"version": 1}
    assert (await client.get("/api/study")).json()["reached_step"] == "search"
    assert (after["confirmed_version"], after["has_unconfirmed_changes"]) == (1, False)
    # Повтор без изменений не плодит версии.
    assert (await client.post("/api/criteria/confirm")).json() == {"version": 1}

    draft["search_criteria"][1]["terms"].append("maximum longevity")
    draft["search_criteria"][1]["origin"] = "edited"
    edited = (await client.put("/api/criteria", json=draft)).json()
    assert (edited["confirmed_version"], edited["has_unconfirmed_changes"]) == (1, True)
    assert edited["search_criteria"][1]["suggested_terms"] == ["lifespan", "longevity"]

    assert (await client.post("/api/criteria/confirm")).json() == {"version": 2}
    assert (await client.get("/api/study")).json()["reached_step"] == "search"


async def test_reformulation_proposes_without_overwriting_edits(signed_in, run_worker, llm):
    client = signed_in
    draft = fields(await prepared(client, run_worker))
    draft["objects"][0]["aliases"] = ["naked mole-rat"]
    draft["objects"][0]["suggested_aliases"] = ["naked mole rat"]
    draft["period"] = {"from_year": 2009, "to_year": 2010}
    draft["search_criteria"] = [
        {
            "id": "crit-new-1",
            "label": "Мой критерий",
            "terms": ["naked mole-rat"],
            "suggested_terms": [],
            "origin": "added",
        }
    ]
    await client.put("/api/criteria", json=draft)
    llm.default = {
        **PROPOSAL,
        "search_criteria": [{"label": "Возраст", "terms": ["age estimate"]}],
    }

    proposal = await client.post("/api/criteria/reformulate")
    kept = (await client.get("/api/criteria")).json()

    assert proposal.status_code == 200
    assert [(c["label"], c["terms"], c["origin"]) for c in proposal.json()["search_criteria"]] == [
        ("Возраст", ["age estimate"], "suggested")
    ]
    assert NAKED_MOLE_RAT in llm.calls[-1].user
    assert kept["search_criteria"] == draft["search_criteria"]
    assert fields(kept) == draft


async def test_suggested_aliases_are_persisted_and_versioned(signed_in, run_worker, llm, db):
    from evidence_compass.criteria import CriteriaVersionRecord

    llm.default = {
        **PROPOSAL,
        "objects": [
            {
                "name": "Голый землекоп",
                "scientific_name": "Heterocephalus glaber",
                "aliases": ["naked mole rat", "naked mole-rat"],
            }
        ],
    }
    draft = fields(await prepared(signed_in, run_worker))
    assert draft["objects"][0]["aliases"] == ["naked mole rat", "naked mole-rat"]
    assert draft["objects"][0]["suggested_aliases"] == ["naked mole rat", "naked mole-rat"]
    assert (await signed_in.post("/api/criteria/confirm")).json() == {"version": 1}
    draft["objects"][0]["aliases"] = ["naked mole-rat"]
    draft["objects"][0]["origin"] = "edited"
    saved = (await signed_in.put("/api/criteria", json=draft)).json()
    assert fields(saved) == draft
    assert (await signed_in.post("/api/criteria/confirm")).json() == {"version": 2}
    versions = (
        await db.scalars(select(CriteriaVersionRecord).order_by(CriteriaVersionRecord.number))
    ).all()
    assert versions[0].fields["objects"][0]["aliases"] == ["naked mole rat", "naked mole-rat"]
    assert versions[1].fields["objects"][0]["aliases"] == ["naked mole-rat"]


async def test_legacy_objects_are_readable_without_rewriting_versions(signed_in, run_worker, db):
    from evidence_compass.criteria import CriteriaDraftRecord, CriteriaVersionRecord

    await prepared(signed_in, run_worker)
    record = await db.scalar(select(CriteriaDraftRecord))
    legacy = {
        **record.fields,
        "objects": [
            {
                key: value
                for key, value in obj.items()
                if key not in ("aliases", "suggested_aliases")
            }
            for obj in record.fields["objects"]
        ],
    }
    record.fields = legacy
    await db.commit()
    assert (await signed_in.post("/api/criteria/confirm")).json() == {"version": 1}
    draft = (await signed_in.get("/api/criteria")).json()
    assert draft["objects"][0]["aliases"] == []
    assert draft["objects"][0]["suggested_aliases"] == []
    assert draft["has_unconfirmed_changes"] is False
    assert (await signed_in.post("/api/criteria/confirm")).json() == {"version": 1}
    version = await db.scalar(select(CriteriaVersionRecord))
    assert version.fields == legacy


async def test_reformulation_failure_keeps_the_criteria(signed_in, run_worker, llm):
    draft = await prepared(signed_in, run_worker)
    llm.replies = [unavailable("время ожидания истекло")]

    refused = await signed_in.post("/api/criteria/reformulate")

    assert refused.status_code == 503
    assert "время ожидания истекло" in refused.json()["message"]
    assert (await signed_in.get("/api/criteria")).json() == draft


async def test_without_a_model_connection_criteria_are_filled_manually(make_client, account):  # noqa: F811
    async with make_client(model=None) as client:
        await sign_in(client)
        await client.put("/api/question", json={"text": NAKED_MOLE_RAT})

        refused = await client.post("/api/question/prepare-criteria")
        assert refused.status_code == 503
        assert "модел" in refused.json()["message"].lower()
        assert (await client.get("/api/question/versions")).json() == []
        assert (await client.get("/api/study")).json()["reached_step"] == "question"

        manual = await client.post("/api/criteria/start-manually")
        draft = manual.json()
        assert manual.status_code == 200
        assert draft["status"] == "ready"
        assert draft["objects"] == [] and draft["search_criteria"] == []
        assert "reformulate" not in draft["allowed_actions"]
        assert (await client.get("/api/study")).json()["reached_step"] == "criteria"
        assert len((await client.get("/api/question/versions")).json()) == 1
        assert (await client.post("/api/criteria/reformulate")).status_code == 503


async def test_worker_restart_resumes_an_interrupted_preparation(signed_in, run_worker, db):
    from evidence_compass import worker
    from tests.conftest import settings_for_tests

    await signed_in.post("/api/question/prepare-criteria")
    operation = await db.scalar(select(OperationRecord))
    operation.status = "running"  # worker упал посреди вызова модели
    await db.commit()

    assert await run_worker() == 0
    await worker.recover(db)
    assert await run_worker(settings_for_tests()) == 1
    assert (await signed_in.get("/api/criteria")).json()["status"] == "ready"


async def test_failed_repreparation_keeps_criteria_tied_to_their_question(
    signed_in, run_worker, llm, db
):
    from evidence_compass.criteria import CriteriaVersionRecord
    from evidence_compass.questions import QuestionVersionRecord

    client = signed_in
    await prepared(client, run_worker)
    await client.put("/api/question", json={"text": TUATARA})
    llm.replies = [unavailable(), unavailable(), unavailable()]

    failed = await prepared(client, run_worker)
    confirmed = await client.post("/api/criteria/confirm")

    # Прежние строки не стёрты отказом и остаются критериями первого вопроса.
    assert failed["status"] == "failed"
    assert [o["scientific_name"] for o in failed["objects"]][0] == "Heterocephalus glaber"
    assert confirmed.json() == {"version": 1}
    version = await db.scalar(select(CriteriaVersionRecord))
    question = await db.get(QuestionVersionRecord, version.question_version_id)
    assert question.text == NAKED_MOLE_RAT

    # «Переформулировать» опирается на текущий вопрос.
    await client.post("/api/criteria/reformulate")
    assert TUATARA in llm.calls[-1].user


async def test_unexpected_worker_error_frees_the_slot(signed_in, run_worker, llm, db):
    llm.replies = [RuntimeError("сбой адаптера")]
    await signed_in.post("/api/question/prepare-criteria")

    with pytest.raises(RuntimeError):
        await run_worker()

    draft = (await signed_in.get("/api/criteria")).json()
    assert draft["status"] == "failed"
    assert (await db.scalar(select(OperationRecord))).status == "failed"
    assert (await signed_in.put("/api/question", json={"text": TUATARA})).status_code == 200
