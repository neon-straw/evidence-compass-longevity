"""Результат на HTTP API: ревизия справки, краткий вывод, охват и основание строки (#17; spec
«Справка и интерфейс», AC10–11).

Работы обрабатывает настоящий `ModelExtractor` с `FakeLLM`; основания сверяются с текстом
загруженного PDF. Краткий вывод собирает сервер по шаблону из допустимых строк.
"""

import json

import pytest
from sqlalchemy import select, update

from evidence_compass import reports
from evidence_compass.cards import MeasureRecord
from evidence_compass.extractor import ModelExtractor
from tests.fake_llm import invalid
from tests.test_cards import card, measure, quote
from tests.test_documents import extraction
from tests.test_extraction import prepared, start
from tests.test_question import account  # noqa: F401
from tests.test_search import confirmed  # noqa: F401
from tests.test_selection import record

ARTICLE = (
    "Results\n"
    "The colony was kept in captivity.\n"
    "The maximum lifespan of 31 years was recorded.\n"
    "Median lifespan was not reached.\n"
    "In a different group, median lifespan was about 31 years (SD 2 years)."
)


@pytest.fixture
def extractor(llm):
    return ModelExtractor(llm, attempts=2)


@pytest.fixture
async def work(confirmed, run_worker, sources):  # noqa: F811
    """Одна выбранная работа, в тексте которой есть значение 31 years."""
    return await prepared(confirmed, run_worker, sources, record(1), text=ARTICLE)


async def extracted(client, run_worker) -> dict:
    assert (await start(client)).status_code == 202
    await run_worker()
    return await result(client)


async def result(client) -> dict:
    response = await client.get("/api/result")
    assert response.status_code == 200, response.text
    return response.json()


async def evidence(client, measure_id: str) -> list[dict]:
    response = await client.get(f"/api/measures/{measure_id}/evidence")
    assert response.status_code == 200, response.text
    return response.json()


async def pinned(db, number: int) -> tuple[list[int], list[int]]:
    """Ревизии карточек и версии строк, закреплённые ревизией справки."""
    report = await db.scalar(
        select(reports.ReportRevisionRecord).where(reports.ReportRevisionRecord.number == number)
    )
    card_revisions = await db.scalars(
        select(reports.ReportCardRevisionRecord.card_revision_id)
        .where(reports.ReportCardRevisionRecord.report_revision_id == report.id)
        .order_by(reports.ReportCardRevisionRecord.card_revision_id)
    )
    rows = await db.scalars(
        select(reports.ReportMeasureRecord.measure_id)
        .where(reports.ReportMeasureRecord.report_revision_id == report.id)
        .order_by(reports.ReportMeasureRecord.number)
    )
    return list(card_revisions), list(rows)


VERIFIED = measure(evidence=[quote("maximum lifespan of 31 years", "страница 1")])
# Цитаты нет в документе.
QUOTE_NOT_FOUND = [quote("maximum lifespan of 45 years")]
# Цитата найдена, но числа 31 в ней нет.
VALUE_NOT_MATCHED = [quote("Median lifespan was not reached.")]


async def extracted_with_review(client, run_worker, db, statuses: dict[int, str]) -> dict:
    """Результат после отметок проверки строк (номер показателя → статус) до первой ревизии.

    Команда проверки — #18; до неё отметка пишется прямо в БД.
    """
    assert (await start(client)).status_code == 202
    await run_worker()
    for number, status in statuses.items():
        await db.execute(
            update(MeasureRecord).where(MeasureRecord.number == number).values(review_status=status)
        )
    await db.commit()
    return await result(client)


async def test_no_result_before_extraction(work):
    assert (await work.get("/api/result")).status_code == 404
    assert (await work.get("/api/study")).json()["reached_step"] == "preparation"


async def test_result_shows_rows_summary_and_coverage_of_one_revision(work, run_worker, llm):
    llm.default = card(VERIFIED)

    found = await extracted(work, run_worker)

    assert (await work.get("/api/study")).json()["reached_step"] == "result"
    assert found["revision"] == {
        "number": 1,
        "summary_revision": 1,
        "freshness": "current",
        "stale_reason": None,
        "complete": True,
    }
    assert found["question"]
    assert {q["resource"] for q in found["search"]["queries"]} == set(found["search"]["resources"])
    [row] = found["measures"]
    assert row["number"] == 1
    assert (row["species_name_in_source"], row["measure_kind"]) == (
        "Heterocephalus glaber",
        "observed_maximum",
    )
    assert (row["value_original"], row["unit_original"]) == ("31", "years")
    assert (row["review_status"], row["verification_status"]) == ("unreviewed", "verified")
    assert row["publication_label"].endswith("Lifespan of captive animals, cohort 1")
    assert row["evidence_ids"] and row["context_evidence_ids"] == []

    summary = found["summary"]
    [finding] = summary["findings"]
    assert finding["measure_refs"] == [row["id"]]
    assert finding["species"] == "Голый землекоп (Heterocephalus glaber)"
    assert finding["items"] == [
        {
            "measure_id": row["id"],
            "number": 1,
            "title": "Наблюдаемый максимум — 31 years",
            "context": "неволя · captive colony",
            "publication": "Author 2020",
            "checked_manually": False,
        }
    ]
    assert finding["text"] == (
        "Голый землекоп (Heterocephalus glaber), Author 2020: наблюдаемый максимум — 31 years, "
        "«captive colony», неволя (строка 01)."
    )
    assert summary["absent_reason"] is None
    # Одна строка не сравнивается ни с чем: о разных определениях не говорится.
    assert reports.DIFFERENT_DEFINITIONS not in summary["limits"]
    assert summary["limits"][0] == (
        "Вывод опирается на 1 из 1 строки; строки ещё не проверены пользователем: это "
        "предварительная выборка, а не научное подтверждение."
    )

    coverage = found["coverage"]
    assert (coverage["found"], coverage["selected"], coverage["full_texts"]) == (1, 1, 1)
    assert (coverage["unavailable"], coverage["not_processed"], coverage["share"]) == (0, 0, 1.0)
    [covered] = coverage["works"]
    assert (covered["outcome"], covered["measure_count"]) == ("measures_extracted", 1)
    # Обновление справки и экспорт — #18 и #19.
    assert found["allowed_actions"] == []


async def test_revision_pins_exact_versions_of_rows_and_cards(
    confirmed,  # noqa: F811
    run_worker,
    sources,
    llm,
    db,
):
    client = await prepared(confirmed, run_worker, sources, record(1), record(2), text=ARTICLE)
    llm.replies = [card(VERIFIED), invalid(), invalid()]

    first = await extracted(client, run_worker)
    again = await result(client)

    # Чтение не создаёт новую ревизию, пока входы те же.
    assert again["revision"]["number"] == first["revision"]["number"] == 1
    assert first["revision"]["complete"] is True
    assert [w["outcome"] for w in first["coverage"]["works"]] == ["measures_extracted", "error"]
    assert (await client.post("/api/extraction/retry-failed")).status_code == 200
    llm.replies = [card(VERIFIED, measure("M2", value_original="22", measure_kind="median"))]
    await run_worker()

    second = await result(client)

    assert second["revision"]["number"] == 2
    assert reports.DIFFERENT_DEFINITIONS in second["summary"]["limits"]
    kept = second["measures"][0]
    assert kept == first["measures"][0]
    assert [row["number"] for row in second["measures"]] == [1, 2, 3]
    await db.rollback()
    first_cards, first_rows = await pinned(db, 1)
    assert first_rows == [int(first["measures"][0]["id"])]
    assert len(first_cards) == 1
    later_cards, later_rows = await pinned(db, 2)
    assert later_cards[0] == first_cards[0] and len(later_cards) == 2
    assert later_rows == [int(row["id"]) for row in second["measures"]]


async def test_without_eligible_rows_coverage_and_reasons_but_no_findings(work, run_worker, llm):
    llm.default = card(
        # Цитаты нет в документе: привязка требует проверки.
        measure(evidence=QUOTE_NOT_FOUND),
        measure("M2", origin_kind="secondary_report", evidence=VERIFIED["evidence"]),
    )

    found = await extracted(work, run_worker)

    assert len(found["measures"]) == 2
    summary = found["summary"]
    assert summary["findings"] == []
    assert summary["absent_reason"].startswith("Нет допустимых строк")
    assert summary["excluded_count"] == 2
    limits = " ".join(summary["excluded_reasons"])
    assert "Строка 01 — число с единицами не найдено в тексте документа по цитате" in limits
    # Ссылки на другую работу рядом может не быть: значение из введения — тоже не результат работы.
    assert "Строка 02 — не собственный результат работы" in limits
    assert "после сверки отметьте строку проверенной" in limits


async def test_verification_gap_tells_apart_missing_quote_and_unmatched_value(
    work, run_worker, llm
):
    llm.default = card(
        VERIFIED,
        measure("M2", evidence=QUOTE_NOT_FOUND),
        measure("M3", evidence=VALUE_NOT_MATCHED),
    )

    found = await extracted(work, run_worker)

    assert [(row["verification_status"], row["verification_gap"]) for row in found["measures"]] == [
        ("verified", None),
        ("needs_review", "quote_not_found"),
        ("needs_review", "value_not_matched"),
    ]


async def test_checked_row_without_automatic_match_enters_the_summary(work, run_worker, llm, db):
    """Автоматическая сверка ошибается (число словами, разбитая таблица PDF): отметка
    пользователя «проверено» допускает строку, и вывод говорит, что сверено вручную."""
    llm.default = card(
        VERIFIED,
        measure("M2", evidence=VALUE_NOT_MATCHED),
        measure("M3", evidence=QUOTE_NOT_FOUND),
    )

    found = await extracted_with_review(work, run_worker, db, {2: "checked", 3: "corrected"})

    summary = found["summary"]
    [finding] = summary["findings"]
    assert [(item["number"], item["checked_manually"]) for item in finding["items"]] == [
        (1, False),
        (2, True),
    ]
    assert (
        "Строка 02 — сверена с документом пользователем, автоматическая сверка не прошла."
        in summary["limits"]
    )
    # Исправленное поле требует повторной проверки (#18): «corrected» не сверка.
    assert summary["excluded_count"] == 1
    assert summary["excluded_reasons"][0].startswith("Строка 03 — число с единицами")


async def test_checked_does_not_lift_other_exclusions(work, run_worker, llm, db):
    llm.default = card(
        measure(origin_kind="secondary_report", evidence=VERIFIED["evidence"]),
        measure("M2", evidence=VERIFIED["evidence"]),
        measure("M3", evidence=QUOTE_NOT_FOUND),
        unresolved=[{"description": "Two values", "measure_keys": ["M2"], "evidence": []}],
    )

    found = await extracted_with_review(
        work, run_worker, db, {1: "checked", 2: "checked", 3: "rejected"}
    )

    summary = found["summary"]
    assert summary["findings"] == []
    assert [reason.split(" — ")[0] for reason in summary["excluded_reasons"]] == [
        "Строка 03",
        "Строка 01",
        "Строка 02",
    ]
    assert summary["excluded_reasons"][0].endswith("отклонены при проверке извлечения.")


async def test_summary_keeps_each_measure_and_qualifier_in_a_species_group(work, run_worker, llm):
    llm.default = card(
        VERIFIED,
        measure(
            "M2",
            measure_kind="median",
            definition="median lifespan",
            qualifier="about",
            uncertainty={
                "label": "SD",
                "kind": "SD",
                "level_percent": None,
                "plus_minus": "2",
                "lower": None,
                "upper": None,
                "upper_status": None,
            },
            evidence=[quote("median lifespan was about 31 years")],
        ),
    )
    found = await extracted(work, run_worker)
    assert found["measures"][1]["verification_status"] == "verified", await evidence(
        work, found["measures"][1]["id"]
    )
    [finding] = found["summary"]["findings"]
    assert [item["title"] for item in finding["items"]] == [
        "Наблюдаемый максимум — 31 years",
        "Медиана — about 31 years (± 2 years; SD)",
    ], found["summary"]
    assert [item["number"] for item in finding["items"]] == [1, 2]
    assert [item["measure_id"] for item in finding["items"]] == [
        row["id"] for row in found["measures"]
    ]


async def test_work_without_measures_gives_coverage_without_summary(work, run_worker, llm):
    llm.default = card()

    found = await extracted(work, run_worker)

    assert found["measures"] == []
    assert found["summary"]["findings"] == []
    assert "не дали опубликованных показателей" in found["summary"]["absent_reason"]
    # «из 1 строки», не «из 1 строка»: правильный падеж после «из».
    [covered] = found["coverage"]["works"]
    assert covered["outcome"] == "no_measures" and covered["reason"]
    assert found["coverage"]["share"] == 0.0


async def test_absent_reason_uses_correct_case_after_of(work, run_worker, llm):
    llm.default = card(measure(origin_kind="secondary_report", evidence=VERIFIED["evidence"]))

    found = await extracted(work, run_worker)

    assert found["summary"]["absent_reason"] == (
        "Нет допустимых строк: ни одна из 1 строки не может войти в вывод. "
        "Причины — ниже и в охвате."
    )


async def test_works_without_measures_list_outcome_and_structured_causes(work, run_worker, llm):
    """AC: причины по работам — структура, не склеенная строка; «Требует решения» первым."""
    llm.default = card(
        set_aside=[
            {"kind": "simulation", "description": "Значение из симуляции.", "evidence": []},
            {"kind": "other_species", "description": "Другой вид.", "evidence": []},
        ],
        unresolved=[
            {
                "description": "Неясно, к какой особи относится оценка.",
                "measure_keys": [],
                "evidence": [],
            }
        ],
    )

    found = await extracted(work, run_worker)

    [absent_work] = found["summary"]["works"]
    assert absent_work["outcome"] == "unresolved"
    assert absent_work["unresolved"] == ["Неясно, к какой особи относится оценка"]
    assert absent_work["set_aside"] == [
        {"kind": "simulation", "description": "Значение из симуляции"},
        {"kind": "other_species", "description": "Другой вид"},
    ]
    assert absent_work["label"] == "Author 2020"
    # Работа без показателей не дублирует ограничения общей сводки склеенной фразой.
    assert not any("Без показателей в результате" in limit for limit in found["summary"]["limits"])


async def test_discrepancy_names_its_row(work, run_worker, llm):
    """Расхождение связано со строкой таблицы: её номер виден и в «Не включены в вывод»."""
    llm.default = card(
        measure(origin_kind="secondary_report", evidence=VERIFIED["evidence"]),
        unresolved=[
            {"description": "Average or maximum?", "measure_keys": ["M1"], "evidence": []},
            {"description": "Group unclear.", "measure_keys": [], "evidence": []},
        ],
    )

    found = await extracted(work, run_worker)

    [absent_work] = found["summary"]["works"]
    assert absent_work["unresolved"] == ["Строка 01 — Average or maximum?", "Group unclear"]
    assert found["summary"]["excluded_reasons"][0].startswith("Строка 01 — не собственный")


async def test_work_with_rows_in_the_summary_is_not_listed_as_without_measures(
    work, run_worker, llm
):
    """Итог `unresolved` бывает и у работы со строками: её допустимая строка входит в вывод,
    а неразрешённая — в «Не включены в вывод»; в списке работ без показателей её нет."""
    llm.default = card(
        VERIFIED,
        measure("M2", evidence=VERIFIED["evidence"]),
        unresolved=[{"description": "Two values", "measure_keys": ["M2"], "evidence": []}],
    )

    found = await extracted(work, run_worker)

    assert found["coverage"]["works"][0]["outcome"] == "unresolved"
    [finding] = found["summary"]["findings"]
    assert [item["number"] for item in finding["items"]] == [1]
    assert found["summary"]["excluded_reasons"][0].startswith("Строка 02 — в источнике расхождение")
    assert found["summary"]["works"] == []


async def test_works_needing_a_decision_are_listed_before_others(
    confirmed,  # noqa: F811
    run_worker,
    sources,
    llm,
):
    client = await prepared(confirmed, run_worker, sources, record(1), record(2), text=ARTICLE)
    llm.replies = [
        card(),
        card(unresolved=[{"description": "Требует решения.", "measure_keys": [], "evidence": []}]),
    ]

    found = await extracted(client, run_worker)

    assert [w["outcome"] for w in found["summary"]["works"]] == ["unresolved", "no_measures"]


async def test_result_has_no_quality_assessment(work, run_worker, llm):
    """AC10: баллов, оценок и категорий качества нет; пустые ограничения авторов — null."""
    llm.default = card(VERIFIED)

    found = await extracted(work, run_worker)

    text = json.dumps(found, ensure_ascii=False).casefold()
    for word in ("quality", "score", "rating", "качеств", "надёжн", "балл"):
        assert word not in text
    assert found["measures"][0]["author_notes"] is None


async def test_author_limitations_are_attached_to_their_rows(work, run_worker, llm):
    llm.default = card(
        measure(limitation_keys=["L1"], evidence=VERIFIED["evidence"]),
        author_limitations=[
            {"key": "L1", "text": "Small colony", "scope": None, "evidence": [quote("colony")]}
        ],
    )

    found = await extracted(work, run_worker)

    assert found["measures"][0]["author_notes"] == "Small colony"


async def test_evidence_shows_the_real_fragment_version_and_page(work, run_worker, llm):
    llm.default = card(
        measure(
            evidence=VERIFIED["evidence"],
            context_evidence=[quote("The colony was kept in captivity.")],
        )
    )
    [row] = (await extracted(work, run_worker))["measures"]

    value, context = await evidence(work, row["id"])

    assert [value["id"], context["id"]] == row["evidence_ids"] + row["context_evidence_ids"]
    assert (value["role"], context["role"]) == ("value", "context")
    assert (value["document_name"], value["document_version"]) == ("Основной текст", 1)
    assert value["locator"]["kind"] == "pdf"
    assert value["locator"]["page"] == 1
    assert value["locator"]["printed"] == "страница 1"
    assert value["verification_status"] == "verified"
    highlighted = [line for line in value["fragment"] if line["highlighted"]]
    # Фрагмент — текст документа, а не цитата модели.
    assert [line["text"] for line in highlighted] == [
        "The maximum lifespan of 31 years was recorded."
    ]
    assert value["precision_note"] is None
    assert value["source"]["identifier"] == "10.1234/work.1"
    assert value["source"]["year"] == 2020
    assert [line["text"] for line in context["fragment"] if line["highlighted"]] == [
        "The colony was kept in captivity."
    ]


async def test_evidence_without_an_address_explains_the_limitation(work, run_worker, llm):
    llm.default = card(measure(evidence=[quote("maximum lifespan of 45 years")]))
    [row] = (await extracted(work, run_worker))["measures"]

    [value] = await evidence(work, row["id"])

    assert value["locator"] is None
    assert value["fragment"] is None
    # Цитата модели показывается как её слова, а не как фрагмент документа.
    assert (value["quote"], value["claimed_location"]) == (
        "maximum lifespan of 45 years",
        "страница 1",
    )
    assert value["verification_status"] == "needs_review"
    assert value["diagnostic"] == "Цитата не найдена в документе."
    assert "цитата не найдена" in value["precision_note"]


async def test_evidence_of_unknown_measure_is_not_found(work):
    assert (await work.get("/api/measures/999/evidence")).status_code == 404
    assert (await work.get("/api/measures/abc/evidence")).status_code == 404


async def test_document_version_file_opens_for_its_owner(work, run_worker, llm):
    llm.default = card(VERIFIED)
    [row] = (await extracted(work, run_worker))["measures"]
    [value] = await evidence(work, row["id"])

    response = await work.get(
        f"/api/documents/{value['document_id']}/versions/{value['document_version']}/file"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content.startswith(b"%PDF")
    main = (await extraction(work))["documents"][0]
    assert main["id"] == value["document_id"]
    assert (await work.get(f"/api/documents/{main['id']}/versions/9/file")).status_code == 404
    assert (await work.get("/api/documents/999/versions/1/file")).status_code == 404
