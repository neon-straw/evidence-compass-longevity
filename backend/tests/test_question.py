"""Вопрос на HTTP API: черновик, неизменяемые версии, отказ вне формы Stage 1.

Граница — маршруты приложения (spec, «Основная граница: API»), без проверки SQL.
"""

import pytest

from evidence_compass.accounts import create_account
from evidence_compass.api.auth import SESSION_COOKIE

LOGIN, PASSWORD = "researcher", "correct horse battery"
OTHER_LOGIN, OTHER_PASSWORD = "reviewer", "another horse"

NAKED_MOLE_RAT = (
    "Какие численные показатели продолжительности жизни опубликованы "
    "для голого землекопа и гренландской акулы?"
)
TUATARA = "Какие показатели продолжительности жизни опубликованы для туатары?"
BRANDT_BAT = "Каков максимальный зарегистрированный возраст Myotis brandtii?"


@pytest.fixture
async def account(db):
    return await create_account(db, LOGIN, PASSWORD)


async def sign_in(client, login=LOGIN, password=PASSWORD):
    response = await client.post("/api/session", json={"login": login, "password": password})
    assert response.status_code == 200, response.text
    return response


async def test_unsaved_question_is_an_empty_draft(client, account):
    await sign_in(client)

    draft = await client.get("/api/question")
    study = await client.get("/api/study")

    assert draft.status_code == 200
    assert draft.json() == {"text": "", "saved_at": None}
    assert study.json() == {"reached_step": "question"}
    assert (await client.get("/api/question/versions")).json() == []
    assert (await client.get("/api/criteria")).status_code == 404


async def test_draft_survives_reload_on_a_new_client(make_client, account):
    async with make_client() as client:
        await sign_in(client)
        saved = await client.put("/api/question", json={"text": TUATARA})
        token = client.cookies[SESSION_COOKIE]

    assert saved.status_code == 200
    assert saved.json()["text"] == TUATARA
    assert saved.json()["saved_at"]

    async with make_client() as again:
        again.cookies.set(SESSION_COOKIE, token)
        loaded = await again.get("/api/question")

    assert loaded.json() == saved.json()


async def test_draft_is_private_to_the_signed_in_account(make_client, db, account):
    await create_account(db, OTHER_LOGIN, OTHER_PASSWORD)
    async with make_client() as owner:
        await sign_in(owner)
        await owner.put("/api/question", json={"text": NAKED_MOLE_RAT})
        assert (await owner.post("/api/question/prepare-criteria")).status_code == 202
    async with make_client() as other:
        await sign_in(other, OTHER_LOGIN, OTHER_PASSWORD)
        assert (await other.get("/api/question")).json()["text"] == ""
        assert (await other.get("/api/question/versions")).json() == []
        assert (await other.get("/api/study")).json()["reached_step"] == "question"


async def test_each_preparation_freezes_a_new_question_version(client, account, run_worker):
    await sign_in(client)
    await client.put("/api/question", json={"text": NAKED_MOLE_RAT})

    first = await client.post("/api/question/prepare-criteria")
    frozen = (await client.get("/api/question/versions")).json()

    assert first.status_code == 202
    assert first.json()["status"] == "preparing"
    assert (await client.get("/api/study")).json()["reached_step"] == "criteria"
    assert (await client.get("/api/criteria")).json()["status"] == "preparing"
    assert frozen[0]["version"] == 1
    assert frozen[0]["text"] == NAKED_MOLE_RAT
    assert frozen[0]["created_at"]

    # Правка вопроса ждёт завершения подготовки: она занимает общий слот (ADR 0003).
    await run_worker()
    draft = await client.put("/api/question", json={"text": TUATARA})
    assert (await client.get("/api/question/versions")).json() == frozen
    assert draft.json()["text"] == TUATARA

    # Повторный запуск той же фразы — новая версия, прежняя не переписывается.
    await client.put("/api/question", json={"text": NAKED_MOLE_RAT})
    assert (await client.post("/api/question/prepare-criteria")).status_code == 202
    await run_worker()
    await client.put("/api/question", json={"text": TUATARA})
    second = await client.post("/api/question/prepare-criteria")
    await run_worker()
    versions = (await client.get("/api/question/versions")).json()

    assert second.status_code == 202
    assert [item["text"] for item in versions] == [NAKED_MOLE_RAT, NAKED_MOLE_RAT, TUATARA]
    assert [item["version"] for item in versions] == [1, 2, 3]
    assert versions[0] == frozen[0]

    cleared = await client.put("/api/question", json={"text": "   "})
    refused = await client.post("/api/question/prepare-criteria")
    assert cleared.status_code == 200
    assert refused.status_code == 422
    assert "example" not in refused.json()
    assert (await client.get("/api/question/versions")).json() == versions


@pytest.mark.parametrize("text", [NAKED_MOLE_RAT, TUATARA, BRANDT_BAT])
async def test_stage_1_question_can_be_prepared(client, account, text):
    await sign_in(client)
    saved = await client.put("/api/question", json={"text": text})

    prepared = await client.post("/api/question/prepare-criteria")
    draft = await client.get("/api/question")
    versions = (await client.get("/api/question/versions")).json()

    assert prepared.status_code == 202
    assert draft.json() == saved.json()
    assert versions[0]["text"] == text
    assert (await client.get("/api/study")).json()["reached_step"] == "criteria"


async def test_blank_question_is_not_prepared(client, account):
    await sign_in(client)
    await client.put("/api/question", json={"text": ""})

    refused = await client.post("/api/question/prepare-criteria")

    assert refused.status_code == 422
    assert refused.json()["message"] == "Сначала сформулируйте вопрос."
    assert "example" not in refused.json()
    assert (await client.get("/api/question/versions")).json() == []
    assert (await client.get("/api/study")).json()["reached_step"] == "question"
