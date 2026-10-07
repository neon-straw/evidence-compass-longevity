"""Временная очистка данных: удаляется вместе с `api/reset.py`."""

from tests.test_question import TUATARA, account, sign_in  # noqa: F401


async def test_clearing_data_starts_over_and_keeps_sign_in(client, account):  # noqa: F811
    await sign_in(client)
    await client.put("/api/question", json={"text": TUATARA})

    cleared = await client.delete("/api/study")

    assert cleared.status_code == 204
    assert (await client.get("/api/question")).json() == {"text": "", "saved_at": None}
    assert (await client.get("/api/study")).json() == {"reached_step": "question"}
