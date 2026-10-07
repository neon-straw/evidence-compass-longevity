"""Извлечение карточек и показателей моделью (#15; spec «Опубликованный показатель», AC07–08).

Worker обрабатывает работы настоящим `ModelExtractor`; вместо OpenRouter — `FakeLLM` с
контролируемыми ответами. Ответ модели проверяется схемой `cards.Card`, ссылки сверяются
сервером; попытка пишет кандидатную ревизию карточки той же транзакцией, что и итог работы.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from evidence_compass import cards, worker
from evidence_compass.documents import DocumentVersionRecord
from evidence_compass.extraction import (
    ExtractionItemRecord,
    SourceDocument,
    WorkInput,
    WorkResult,
)
from evidence_compass.extractor import ModelExtractor, prompt
from evidence_compass.operations import OperationRecord
from tests.fake_extractor import Crash
from tests.fake_llm import ACTUAL_MODEL, ACTUAL_PROVIDER, invalid, unavailable
from tests.test_documents import extraction
from tests.test_extraction import prepared, start
from tests.test_question import account  # noqa: F401
from tests.test_search import confirmed  # noqa: F401
from tests.test_selection import record

MOLE_RAT = "Голый землекоп (Heterocephalus glaber)"


@pytest.fixture
def extractor(llm):
    return ModelExtractor(llm, attempts=2)


@pytest.fixture
async def work(confirmed, run_worker, sources):  # noqa: F811
    """Одна выбранная работа с готовым основным текстом."""
    return await prepared(confirmed, run_worker, sources, record(1))


def quote(text: str = "maximum lifespan of 31 years", location: str = "страница 1") -> dict:
    return {"document": "D1", "location": location, "text": text}


def measure(key: str = "M1", **fields) -> dict:
    return {
        "key": key,
        "species_name_in_source": "Heterocephalus glaber",
        "species_label": MOLE_RAT,
        "taxonomy_note": None,
        "measure_kind": "observed_maximum",
        "definition": "maximum recorded lifespan",
        "value_original": "31",
        "unit_original": "years",
        "qualifier": None,
        "uncertainty": None,
        "subject": {
            "kind": "group",
            "label": "captive colony",
            "sex": None,
            "sample_size": None,
            "sample_size_scope": None,
        },
        "conditions": {"setting": "captive", "text": "laboratory colony"},
        "method": None,
        "origin_kind": "own_result",
        "evidence": [quote()],
        "context_evidence": [],
        "limitation_keys": [],
        "related_keys": [],
        **fields,
    }


def card(*measures: dict, **fields) -> dict:
    return {
        "study_object": "Heterocephalus glaber",
        "subjects": [{"text": "Captive colony", "evidence": [quote("colony")]}],
        "methods": [],
        "results": [],
        "conclusions": [],
        "author_limitations": [],
        "measures": list(measures),
        "unresolved": [],
        "set_aside": [],
        **fields,
    }


async def publication_id(client) -> int:
    return int((await extraction(client))["startable_publication_ids"][0])


async def history(db, publication: int) -> list[cards.CardRevision]:
    await db.rollback()
    return await cards.history(db, publication)


async def main_version_id(db, client) -> int:
    row = (await extraction(client))["documents"][0]
    return await db.scalar(
        select(DocumentVersionRecord.id).where(DocumentVersionRecord.document_id == int(row["id"]))
    )


def work_run(found: dict) -> dict:
    return found["operation"]["works"][0]


async def test_extracted_measures_form_a_candidate_card_revision(work, run_worker, llm, db):
    llm.default = card(
        measure(limitation_keys=["L1"]),
        measure(
            "M2",
            measure_kind="median",
            definition="median lifespan",
            value_original="22",
            related_keys=["M1"],
        ),
        author_limitations=[
            {"key": "L1", "text": "Small colony", "scope": None, "evidence": [quote("small")]}
        ],
    )
    publication = await publication_id(work)
    version_id = await main_version_id(db, work)

    await start(work)
    await run_worker()

    run = work_run(await extraction(work))
    assert (run["execution"], run["outcome"], run["measure_count"]) == (
        "done",
        "measures_extracted",
        2,
    )
    [revision] = await history(db, publication)
    assert revision.number == 1
    assert (revision.model, revision.provider) == (ACTUAL_MODEL, ACTUAL_PROVIDER)
    assert revision.criteria_version_id is not None
    first, second = revision.measures
    # Номера, статусы и ссылки задаёт сервер, а не модель.
    assert (first.number, second.number) == (1, 2)
    assert (first.measure_kind, second.measure_kind) == ("observed_maximum", "median")
    assert {m.review_status for m in revision.measures} == {"unreviewed"}
    # Цитат ответа нет в загруженном PDF: адреса нет, привязка требует проверки.
    assert {m.verification_status for m in revision.measures} == {"needs_review"}
    assert first.content["evidence"] == [
        {
            "document_version_id": version_id,
            "location": "страница 1",
            "quote": "maximum lifespan of 31 years",
            "locator": None,
            "verification_status": "needs_review",
            "diagnostic": "Цитата не найдена в документе.",
        }
    ]
    assert first.content["author_limitation_numbers"] == [1]
    assert second.content["related_measure_numbers"] == [1]
    assert "key" not in first.content
    assert revision.content["author_limitations"][0]["number"] == 1
    # Фактическая модель сохраняется в операции (ADR 0007).
    operation = await db.scalar(select(OperationRecord))
    assert (operation.model, operation.provider) == (ACTUAL_MODEL, ACTUAL_PROVIDER)


async def test_prompt_marks_documents_places_and_study_objects(work, run_worker, llm):
    llm.default = card()
    await start(work)
    await run_worker()

    call = llm.calls[-1]
    assert call.schema is cards.Card
    assert f"Объекты исследования (species_label): {MOLE_RAT}" in call.user
    assert "=== D1: Основной текст — основной текст, PDF ===" in call.user
    assert "[страница 1]\nLifespan of captive animals, cohort 1" in call.user


def test_model_cannot_set_ids_statuses_or_freshness():
    schema = str(cards.Card.model_json_schema())
    for server_field in (
        "'id'",
        "revision_id",
        "review_status",
        "verification_status",
        "freshness",
    ):
        assert server_field not in schema
    with pytest.raises(ValidationError):
        cards.Card.model_validate(card(measure(review_status="checked")))
    with pytest.raises(ValidationError):
        cards.Card.model_validate(card(measure(id="m-1")))


def test_one_measure_has_one_subject_one_setting_and_one_kind():
    """AC07: группы, условия и типы показателей не объединяются в одну строку."""
    groups = [measure()["subject"], {**measure()["subject"], "label": "wild colony"}]
    with pytest.raises(ValidationError):
        cards.Card.model_validate(card(measure(subject=groups)))
    with pytest.raises(ValidationError):
        cards.Card.model_validate(card(measure(measure_kind=["mean", "median"])))
    with pytest.raises(ValidationError):
        cards.Card.model_validate(
            card(measure(conditions={"setting": "wild and captive", "text": ""}))
        )
    with pytest.raises(ValidationError):
        cards.Card.model_validate(card(measure(measure_kind="lifespan")))


async def test_groups_and_conditions_stay_separate_rows(work, run_worker, llm, db):
    wild = {"setting": "wild", "text": "field population"}
    llm.default = card(
        measure("M1", measure_kind="mean", definition="mean lifespan", value_original="10"),
        measure(
            "M2",
            measure_kind="mean",
            definition="mean lifespan",
            value_original="4",
            conditions=wild,
        ),
        measure("M3", measure_kind="observed_maximum", value_original="31"),
    )
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    [revision] = await history(db, publication)
    rows = [
        (m.measure_kind, m.content["conditions"]["setting"], m.content["value_original"])
        for m in revision.measures
    ]
    assert rows == [
        ("mean", "captive", "10"),
        ("mean", "wild", "4"),
        ("observed_maximum", "captive", "31"),
    ]


async def test_other_species_and_simulations_do_not_become_rows(work, run_worker, llm, db):
    """AC07–08: другой вид отложен с причиной, симуляции — не наблюдения."""
    elephant = measure(
        "M2",
        species_name_in_source="Elephas maximus",
        species_label="Elephas maximus",
        value_original="41.7",
        related_keys=["M1"],
    )
    simulation = {
        "kind": "simulation",
        "description": "Simulated cohorts",
        "evidence": [quote("simulated")],
    }
    llm.default = card(
        measure(species_label="heterocephalus glaber", related_keys=["M2"]),
        elephant,
        set_aside=[simulation],
    )
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    run = work_run(await extraction(work))
    assert (run["outcome"], run["measure_count"]) == ("measures_extracted", 1)
    assert run["detail"].startswith("Не стали показателями: 2 записи")
    [revision] = await history(db, publication)
    [row] = revision.measures
    # Подпись группировки нормализована к объекту исследования, исходное имя сохранено.
    assert row.content["species_label"] == MOLE_RAT
    assert row.content["species_name_in_source"] == "Heterocephalus glaber"
    assert row.content["related_measure_numbers"] == []
    kinds = [item["kind"] for item in revision.content["set_aside"]]
    assert kinds == ["simulation", "other_species"]
    assert "Elephas maximus" in revision.content["set_aside"][1]["description"]


async def test_measure_without_evidence_is_set_aside(work, run_worker, llm, db):
    """Без цитаты значение — утверждение модели, а не извлечение: строкой оно не становится."""
    llm.default = card(measure(evidence=[]))
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    run = work_run(await extraction(work))
    assert (run["outcome"], run["measure_count"]) == ("no_measures", 0)
    assert run["detail"].startswith("Не стали показателями: 1 запись")
    [revision] = await history(db, publication)
    assert revision.measures == []
    [aside] = revision.content["set_aside"]
    assert aside["kind"] == "other"
    assert aside["description"].startswith("Нет цитаты из документа: Heterocephalus glaber")
    assert aside["evidence"] == []


async def test_empty_response_is_no_measures_with_a_card(work, run_worker, llm, db):
    llm.default = card()
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    run = work_run(await extraction(work))
    assert (run["outcome"], run["measure_count"], run["detail"]) == ("no_measures", 0, None)
    [revision] = await history(db, publication)
    assert revision.measures == []
    assert revision.content["subjects"][0]["text"] == "Captive colony"


async def test_discrepancy_is_unresolved_and_keeps_both_values(work, run_worker, llm, db):
    llm.default = card(
        measure("M1", value_original="31"),
        measure("M2", value_original="28"),
        unresolved=[
            {
                "description": "Maximum lifespan differs between abstract and table.",
                "measure_keys": ["M1", "M2"],
                "evidence": [quote()],
            }
        ],
    )
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    run = work_run(await extraction(work))
    assert (run["outcome"], run["measure_count"]) == ("unresolved", 2)
    assert run["detail"] == (
        "Требуют решения: 1 (Maximum lifespan differs between abstract and table)."
    )
    [revision] = await history(db, publication)
    assert revision.content["unresolved"][0]["measure_numbers"] == [1, 2]


async def test_reference_to_an_unknown_document_is_an_error_without_a_card(
    work, run_worker, llm, db
):
    llm.default = card(measure(evidence=[{**quote(), "document": "D9"}]))
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    run = work_run(await extraction(work))
    assert (run["execution"], run["outcome"]) == ("failed", "error")
    assert run["detail"] == "Ответ модели отклонён: ссылка на неизвестный документ D9."
    assert await history(db, publication) == []


async def requeue(db, publication: int) -> None:
    """Новая попытка той же работы по тому же снимку документов (повторное извлечение)."""
    await db.rollback()
    item = await db.scalar(
        select(ExtractionItemRecord).where(ExtractionItemRecord.publication_id == publication)
    )
    earlier = await db.get(OperationRecord, item.operation_id)
    operation = OperationRecord(
        study_id=earlier.study_id,
        kind="extraction",
        status="queued",
        input=earlier.input,
        created_at=earlier.created_at,
    )
    db.add(operation)
    await db.flush()
    db.add(
        ExtractionItemRecord(
            operation_id=operation.id,
            publication_id=publication,
            document_versions=item.document_versions,
            gaps=[],
        )
    )
    await db.commit()


async def test_failed_attempt_keeps_the_previous_revision(work, run_worker, llm, db):
    llm.default = card(measure())
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    await requeue(db, publication)
    llm.default = card(measure(evidence=[{**quote(), "document": "D9"}]))
    await run_worker()
    assert work_run(await extraction(work))["outcome"] == "error"
    [kept] = await history(db, publication)
    assert kept.number == 1

    await requeue(db, publication)
    llm.default = card()
    await run_worker()
    assert [revision.number for revision in await history(db, publication)] == [1, 2]


async def test_model_is_retried_within_the_budget_without_changing(work, run_worker, llm):
    llm.replies = [unavailable(), card(measure())]
    await start(work)
    await run_worker()

    assert work_run(await extraction(work))["outcome"] == "measures_extracted"
    card_calls = [call for call in llm.calls if call.schema is cards.Card]
    assert len(card_calls) == 2
    assert card_calls[0].user == card_calls[1].user


async def test_exhausted_budget_waits_for_the_model(work, run_worker, llm, db):
    llm.replies = [unavailable("OpenRouter ответил 502"), unavailable("OpenRouter ответил 503")]
    publication = await publication_id(work)
    await start(work)
    await run_worker()

    found = await extraction(work)
    assert found["operation"]["status"] == "waiting_llm"
    assert work_run(found)["execution"] == "queued"
    log = [event["message"] for event in (await work.get("/api/extraction/log")).json()]
    assert log[-1] == "Модель не ответила: OpenRouter ответил 503; попыток: 2. Продолжите явно."
    assert await history(db, publication) == []

    llm.default = card(measure())
    await work.post("/api/extraction/resume")
    await run_worker()
    assert work_run(await extraction(work))["outcome"] == "measures_extracted"


async def test_crash_after_the_model_answer_does_not_duplicate_the_revision(
    work, run_worker, llm, db, monkeypatch
):
    from evidence_compass import extraction as lifecycle

    llm.default = card(measure())
    publication = await publication_id(work)
    save = lifecycle._save

    async def crash(*args, **kwargs):
        raise Crash

    monkeypatch.setattr(lifecycle, "_save", crash)
    await start(work)
    with pytest.raises(Crash):
        await run_worker()
    assert await history(db, publication) == []

    monkeypatch.setattr(lifecycle, "_save", save)
    await worker.recover(db)
    await db.commit()
    await run_worker()
    assert len(await history(db, publication)) == 1


def source(**fields) -> SourceDocument:
    defaults = {
        "document_id": 1,
        "version": 1,
        "version_id": 11,
        "name": "Основной текст",
        "role": "main",
        "format": "pdf",
        "parse_status": "parsed",
        "content": {"pages": ["Text"]},
    }
    return SourceDocument(**{**defaults, **fields})


def work_input(*documents: SourceDocument) -> WorkInput:
    return WorkInput(1, "Title", "Question?", [], None, list(documents))


async def test_work_without_full_text_is_unavailable(llm):
    abstract_only = source(format="jats", parse_status="partial", content={"blocks": []})

    result = await ModelExtractor(llm, 2).extract(work_input(abstract_only))

    assert result == WorkResult(
        "unavailable",
        None,
        "В документах работы нет полного текста: извлекать показатели не из чего.",
    )
    assert llm.calls == []


def test_raw_csv_records_are_not_sent_to_the_model():
    """AC08: большой исходный массив не превращается в строки — модель видит только заголовки."""
    raw = source(
        document_id=2,
        version_id=12,
        name="supp1.csv",
        role="supplement",
        format="csv",
        content={"header": ["animal", "age"], "records": 3000},
    )
    jats = source(
        document_id=3,
        version_id=13,
        format="jats",
        content={
            "blocks": [{"text": "Results paragraph."}],
            "tables": [{"label": "Table 1", "caption": "Ages", "rows": [["a", "1"]]}],
        },
    )

    request = prompt(work_input(raw, jats))

    assert request is not None
    # Основной текст идёт первым, приложения — после него.
    assert request.documents == {"D1": 13, "D2": 12}
    assert "[§1] Results paragraph." in request.user
    assert "[Table 1 Ages]\n  строка 1: a | 1" in request.user
    assert "[заголовки] animal | age\nЗаписей: 3000." in request.user


def test_small_csv_is_sent_by_records():
    """Малая таблица приложения видна модели по записям: ячейка — адрес основания."""
    table = source(
        document_id=2,
        version_id=12,
        name="supp2.csv",
        role="supplement",
        format="csv",
        content={
            "header": ["group", "median (years)"],
            "records": 2,
            "rows": [["captive,\nlab", "22"], ["wild", "9"]],
        },
    )

    request = prompt(work_input(source(), table))

    assert request is not None
    assert (
        "[заголовки] group | median (years)\n[запись 1] captive, lab | 22\n[запись 2] wild | 9"
        in request.user
    )


async def test_evidence_is_located_in_the_documents_of_the_work(llm):
    text = "The oldest captive male lived 31 years."
    llm.replies = [card(measure(evidence=[quote("lived 31 years")]))]

    result = await ModelExtractor(llm, 2).extract(work_input(source(content={"pages": [text]})))

    assert result.card is not None
    [row] = result.card.measures
    assert row["verification_status"] == "verified"
    assert row["content"]["evidence"][0]["locator"] == {"kind": "pdf", "page": 1}


async def test_answers_outside_the_schema_are_an_error_of_the_work(llm):
    """Ответ вне схемы — не отказ модели: работа завершается ошибкой, операция не ждёт."""
    llm.replies = [invalid(), invalid()]

    result = await ModelExtractor(llm, 2).extract(work_input(source()))

    assert (result.outcome, result.card) == ("error", None)
    assert result.detail == (
        "Ответ модели не прошёл проверку схемы: ответ модели не соответствует ожидаемой "
        "структуре; попыток: 2."
    )


async def test_supplement_text_does_not_replace_the_main_full_text(llm):
    abstract_only = source(format="jats", parse_status="partial", content={"blocks": []})
    supplement = source(document_id=2, version_id=12, name="S1.pdf", role="supplement")

    result = await ModelExtractor(llm, 2).extract(work_input(abstract_only, supplement))

    assert result.outcome == "unavailable"
    assert llm.calls == []


@pytest.mark.parametrize(
    ("kind", "definition", "flagged"),
    [
        ("mean", "average maximum lifespan", True),
        ("mean", "median survival of zoo-born females", True),
        ("median", "maximum recorded lifespan", True),
        ("observed_maximum", "mean lifespan", True),
        ("mean", "средняя продолжительность жизни", False),
        ("median", "median lifespan", False),
        ("observed_maximum", "maximum recorded lifespan", False),
        ("observed_maximum", "latest recorded death age", False),
        ("individual_age", "maximum age of the record individual", False),
    ],
)
def test_kind_contradicting_its_definition_is_unresolved(kind, definition, flagged):
    """Тип показателя, противоречащий определению автора, не проходит молча (AC07)."""
    reply = cards.Card.model_validate(card(measure(measure_kind=kind, definition=definition)))

    candidate = cards.check(reply, {"D1": 11}, {})

    unresolved = candidate.content["unresolved"]
    assert bool(unresolved) is flagged
    if flagged:
        assert unresolved[0]["measure_numbers"] == [1]
        assert definition in unresolved[0]["description"]
        assert unresolved[0]["evidence"] == candidate.measures[0]["content"]["evidence"]


def test_kind_check_does_not_repeat_a_discrepancy_the_model_reported():
    """Модель уже отметила показатель: второе описание той же проблемы не добавляется."""
    reply = cards.Card.model_validate(
        card(
            measure(measure_kind="observed_maximum", definition="average maximum lifespan"),
            unresolved=[
                {"description": "Average or maximum?", "measure_keys": ["M1"], "evidence": []}
            ],
        )
    )

    candidate = cards.check(reply, {"D1": 11}, {})

    assert [item["description"] for item in candidate.content["unresolved"]] == [
        "Average or maximum?"
    ]


@pytest.mark.parametrize("value", ["null", "N/A", "", "не указано", "several decades"])
def test_value_without_a_number_is_not_a_measure(value):
    """Строка без числа — не опубликованный показатель: она отложена с причиной, не потеряна."""
    reply = cards.Card.model_validate(
        card(measure("M1", related_keys=["M2"]), measure("M2", value_original=value))
    )

    candidate = cards.check(reply, {"D1": 11}, {})

    [row] = candidate.measures
    assert row["content"]["related_measure_numbers"] == []
    [aside] = candidate.content["set_aside"]
    assert aside["kind"] == "other"
    assert aside["description"].startswith("Значение не указано числом:")
    assert aside["evidence"][0]["quote"] == "maximum lifespan of 31 years"


def test_measure_without_evidence_drops_references_to_it():
    reply = cards.Card.model_validate(
        card(measure("M1", related_keys=["M2"]), measure("M2", evidence=[]))
    )

    candidate = cards.check(reply, {"D1": 11}, {})

    [row] = candidate.measures
    assert row["content"]["related_measure_numbers"] == []
    [aside] = candidate.content["set_aside"]
    assert aside["description"].startswith("Нет цитаты из документа:")


@pytest.mark.parametrize("value", ["31", "16.9", "12–15", "2,5", "~40"])
def test_numeric_values_stay_measures(value):
    reply = cards.Card.model_validate(card(measure(value_original=value)))

    assert len(cards.check(reply, {"D1": 11}, {}).measures) == 1
