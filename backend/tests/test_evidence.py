"""Адресуемое основание (#16; spec «Адресуемое основание», AC09).

Сервер сам находит цитату модели в разобранной версии документа и записывает адрес: страницу
PDF, узел или ячейку таблицы JATS, ячейку записи CSV. Привязка `verified` только при
уверенном совпадении; иначе `needs_review` без выдуманного адреса.
"""

import pytest

from evidence_compass import cards, evidence
from evidence_compass.parsing import parse
from tests.fake_documents import pdf
from tests.test_cards import card, measure, quote, source

PDF_ID, JATS_ID, CSV_ID = 11, 12, 13


def pages(*texts: str):
    return source(version_id=PDF_ID, content={"pages": list(texts)})


def jats_document():
    xml = (
        b'<article><body><sec><p id="p1">Captive colony of 12 animals.</p>'
        b'<p id="p2">The oldest male lived 31 years in captivity.</p></sec>'
        b'<sec><table-wrap id="t1"><label>Table 1.</label><caption>Lifespan by group</caption>'
        b"<table><tr><th>Group</th><th>Median (years)</th><th>Maximum (years)</th></tr>"
        b"<tr><td>captive</td><td>22</td><td>31</td></tr>"
        b"<tr><td>wild</td><td>9</td><td>17</td></tr></table></table-wrap>"
        b'<table-wrap id="t2"><label>Table 2.</label><table><tr><th>Age</th><th>Age</th></tr>'
        b"<tr><td>30</td><td>31</td></tr></table></table-wrap></sec></body></article>"
    )
    return source(version_id=JATS_ID, format="jats", content=parse(xml, "article.xml").content)


def csv_document():
    data = b'animal,note,age (years)\nA1,"caught\nas adult",17\nA2,short,19\n'
    return source(
        version_id=CSV_ID,
        role="supplement",
        format="csv",
        content=parse(data, "ages.csv").content,
    )


def verified(*documents, **card_fields) -> cards.Candidate:
    """Ответ модели после сверки ключей и проверки оснований по документам."""
    keys = {f"D{number}": document.version_id for number, document in enumerate(documents, 1)}
    reply = cards.Card.model_validate(card(**card_fields))
    candidate = cards.check(reply, keys, {})
    return evidence.verify(candidate, {document.version_id: document for document in documents})


def one_measure(document, **fields) -> dict:
    [row] = verified(document, measures=[measure(**fields)]).measures
    return row


def cited(text: str, location: str = "страница 1") -> list[dict]:
    return [quote(text, location)]


# ---------- PDF ----------


def test_quote_with_value_and_unit_is_verified_with_its_page():
    document = pages("Introduction.", "The maximum lifespan of 31 years was recorded.")

    row = one_measure(document, evidence=cited("maximum lifespan of 31 years", "страница 2"))

    assert row["verification_status"] == "verified"
    [item] = row["content"]["evidence"]
    assert item["locator"] == {"kind": "pdf", "page": 2}
    assert (item["verification_status"], item["diagnostic"]) == ("verified", None)
    assert item["location"] == "страница 2"


def test_quote_without_the_value_needs_review():
    """Реальный случай #16: цитата найдена, но значения 174 в ней нет."""
    text = (
        "Animals with missing age data were excluded from analyses involving age (N=3 individuals)."
    )
    row = one_measure(
        pages(text),
        measure_kind="other",
        definition="individual maximum lifespan",
        value_original="174",
        unit_original="months",
        evidence=cited(text),
    )

    assert row["verification_status"] == "needs_review"
    [item] = row["content"]["evidence"]
    # Цитата действительно на странице: адрес настоящий, но значение им не подтверждено.
    assert item["locator"] == {"kind": "pdf", "page": 1}
    assert item["verification_status"] == "needs_review"
    assert "174" in item["diagnostic"]


def test_number_of_another_quantity_needs_review():
    """Реальный случай #16: 92.4 в цитате есть, но это ml O2 h−1, а не months."""
    text = (
        "Previous values of RMR for naked mole-rats ranged from 21.5 ml O2 h−1 to 92.4 ml O2 h−1."
    )
    row = one_measure(
        pages(text),
        value_original="92.4",
        unit_original="months",
        origin_kind="secondary_report",
        evidence=cited(text),
    )

    assert row["verification_status"] == "needs_review"
    assert "months" in row["content"]["evidence"][0]["diagnostic"]


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("Twelve of 12 animals died within the first year", "12", "years"),
        ("mortality was 0.5 per year", "0.5", "years"),
        ("mortality was 0.5/yr", "0.5", "years"),
        ("lifespan 92.4", "92.4", ""),
        # «+» после числа — оговорка, а не связанное число: 40 + 12 years — не 40 years.
        ("aged 40 + 12 years", "40", "years"),
    ],
)
def test_unit_must_follow_the_value(text, value, unit):
    row = one_measure(pages(text), value_original=value, unit_original=unit, evidence=cited(text))

    assert row["verification_status"] == "needs_review"


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("lived 41-year-old", "41", "years"),
        ("survived 174 d after release", "174", "days"),
        ("range 12–15 years", "12–15", "years"),
        # Оговорка источника у числа: «40+», «>40», «~40».
        ("its extreme longevity (40+ years, Ruby et al., 2018)", "40", "years"),
        ("lifespan >40 years", "40", "years"),
        ("about ~40 yrs", "40", "years"),
    ],
)
def test_unit_right_after_the_value_is_accepted(text, value, unit):
    row = one_measure(pages(text), value_original=value, unit_original=unit, evidence=cited(text))

    assert row["verification_status"] == "verified"


def test_missing_quote_has_no_address():
    row = one_measure(pages("Nothing relevant here."))

    [item] = row["content"]["evidence"]
    assert (item["locator"], item["verification_status"]) == (None, "needs_review")
    assert item["diagnostic"] == "Цитата не найдена в документе."
    assert row["verification_status"] == "needs_review"


@pytest.mark.parametrize(
    ("location", "page", "status"),
    [("страница 2", 2, "verified"), ("страница 1", 1, "verified"), ("", None, "needs_review")],
)
def test_same_quote_on_two_pages_is_resolved_only_by_the_models_place(location, page, status):
    document = pages("One male lived 41 years.", "One male lived 41 years.")

    row = one_measure(document, value_original="41", evidence=cited("lived 41 years", location))

    [item] = row["content"]["evidence"]
    assert item["verification_status"] == status
    assert item["locator"] == (page and {"kind": "pdf", "page": page})
    if page is None:
        assert "страницы 1, 2" in item["diagnostic"]


@pytest.mark.parametrize("text", ["It lived 141 years.", "It lived 4.1 years.", "41.5 years"])
def test_short_number_is_not_matched_inside_another_number(text):
    row = one_measure(pages(text), value_original="41", evidence=cited("41 years"))

    assert row["verification_status"] == "needs_review"
    assert row["content"]["evidence"][0]["locator"] is None


def test_neighbouring_numbers_do_not_merge():
    row = one_measure(
        pages("Banded in 1962 41 years before recapture."),
        value_original="41",
        evidence=cited("41 years before recapture"),
    )

    assert row["verification_status"] == "verified"


def test_value_inside_a_longer_number_of_the_quote_needs_review():
    row = one_measure(
        pages("It lived 141 years."), value_original="41", evidence=cited("lived 141 years")
    )

    assert row["verification_status"] == "needs_review"
    assert row["content"]["evidence"][0]["locator"] == {"kind": "pdf", "page": 1}


def test_pdf_line_breaks_hyphenation_and_ligatures_do_not_hide_the_quote():
    document = source(
        version_id=PDF_ID,
        content=parse(
            pdf("Intro", "the maximum life-\nspan of M. brandtii finally\nexceeded 41 years"),
            "a.pdf",
        ).content,
    )

    row = one_measure(
        document,
        value_original="41",
        evidence=cited("maximum lifespan of M. brandtii finally exceeded 41 years", "страница 2"),
    )

    assert row["verification_status"] == "verified"
    assert row["content"]["evidence"][0]["locator"] == {"kind": "pdf", "page": 2}


def test_ligatures_and_typographic_quotes_do_not_hide_the_quote():
    row = one_measure(
        pages("‘‘why’’M. lucifugus ﬁbroblasts live up to 34 years"),
        value_original="34",
        evidence=cited('"why" M. lucifugus fibroblasts live up to 34 years'),
    )

    assert row["verification_status"] == "verified"


def test_punctuation_at_the_edges_of_the_quote_is_not_required():
    """Реальный случай корпуса: модель закончила цитату точкой, в тексте дальше ссылка."""
    row = one_measure(
        pages("over the 30\nyear timespan covered by the data set ( Figure 1A ). Next"),
        value_original="30",
        evidence=cited("over the 30 year timespan covered by the data set."),
    )

    assert row["content"]["evidence"][0]["locator"] == {"kind": "pdf", "page": 1}


def test_quote_across_a_page_break_points_to_its_first_page():
    document = pages("Methods.\nThe oldest male was", "recaptured at 41 years of age.")

    row = one_measure(
        document,
        value_original="41",
        evidence=cited("The oldest male was recaptured at 41 years", "страница 1"),
    )

    [item] = row["content"]["evidence"]
    assert item["locator"] == {"kind": "pdf", "page": 1}
    assert item["verification_status"] == "verified"
    assert item["diagnostic"] == "Цитата продолжается на странице 2."


def test_value_with_uncertainty_before_the_unit_is_verified():
    row = one_measure(
        pages("Shark no. 4 was 392 ± 120 years old."),
        value_original="392",
        evidence=cited("392 ± 120 years"),
    )

    assert row["verification_status"] == "verified"


def test_place_named_by_the_model_is_corrected_by_the_document():
    row = one_measure(pages("Intro.", "maximum lifespan of 31 years"))

    [item] = row["content"]["evidence"]
    assert (item["locator"], item["verification_status"]) == (
        {"kind": "pdf", "page": 2},
        "verified",
    )
    assert item["diagnostic"] == "Модель указала «страница 1», цитата найдена на странице 2."


# ---------- JATS ----------


def test_jats_quote_points_to_the_node():
    row = one_measure(
        jats_document(),
        evidence=cited("The oldest male lived 31 years", "§2"),
    )

    assert row["verification_status"] == "verified"
    assert row["content"]["evidence"][0]["locator"] == {
        "kind": "jats",
        "path": "/article/body[1]/sec[1]/p[2]",
        "id": "p2",
    }


def test_jats_table_value_points_to_the_cell_with_its_header():
    row = one_measure(
        jats_document(),
        measure_kind="median",
        definition="median lifespan",
        value_original="22",
        evidence=cited(
            "captive 22 31", "Table 1. Lifespan by group строка 2, столбец Median (years)"
        ),
    )

    assert row["verification_status"] == "verified"
    assert row["content"]["evidence"][0]["locator"] == {
        "kind": "jats_cell",
        "path": "/article/body[1]/sec[2]/table-wrap[1]",
        "id": "t1",
        "label": "Table 1.",
        "row": 2,
        "column": 2,
        "headers": ["Median (years)"],
    }


def test_jats_neighbour_cell_needs_review():
    """Значение из соседнего столбца не подменяет значение названного столбца."""
    row = one_measure(
        jats_document(),
        measure_kind="median",
        definition="median lifespan",
        value_original="31",
        evidence=cited("captive 22 31", "Table 1 строка 2, столбец Median (years)"),
    )

    assert row["verification_status"] == "needs_review"
    assert "Median (years)" in row["content"]["evidence"][0]["diagnostic"]


def test_jats_neighbour_row_needs_review():
    row = one_measure(
        jats_document(),
        value_original="17",
        evidence=cited("wild 9 17", "Table 1 строка 2, столбец Maximum (years)"),
    )

    [item] = row["content"]["evidence"]
    assert item["verification_status"] == "needs_review"
    assert item["locator"]["row"] == 3
    assert "строка 2" in item["diagnostic"]


def test_table_caption_does_not_give_units_to_another_column():
    xml = (
        b'<article><body><sec><p>Text.</p><table-wrap id="t3"><label>Table 3.</label>'
        b"<caption>Body mass (g) and lifespan (years)</caption><table>"
        b"<tr><th>Group</th><th>Mass (g)</th></tr><tr><td>wild</td><td>35</td></tr>"
        b"</table></table-wrap></sec></body></article>"
    )
    document = source(version_id=JATS_ID, format="jats", content=parse(xml, "a.xml").content)

    row = one_measure(
        document,
        value_original="35",
        evidence=cited("wild 35", "Table 3 строка 2, столбец Mass (g)"),
    )

    assert row["verification_status"] == "needs_review"


@pytest.mark.parametrize("location", ["Table 2 строка 2, столбец Age", "Table 2 строка 2"])
def test_repeated_jats_headers_without_a_column_position_need_review(location):
    row = one_measure(
        jats_document(),
        value_original="31",
        unit_original="years",
        evidence=cited("30 31", location),
    )

    assert row["verification_status"] == "needs_review"


# ---------- CSV ----------


def test_csv_value_points_to_the_record_and_cell_not_the_physical_line():
    """AC09: многострочная ячейка — одна запись; адрес по индексу записи."""
    document = csv_document()
    row = one_measure(
        document,
        value_original="19",
        evidence=cited("A2 short 19", "запись 2, столбец age (years)"),
    )

    assert row["verification_status"] == "verified"
    assert row["content"]["evidence"][0]["locator"] == {
        "kind": "csv",
        "record": 2,
        "column": 3,
        "header": "age (years)",
        "value": "19",
        "dialect": document.content["dialect"],
        "encoding": "utf-8",
    }


def test_csv_neighbour_record_needs_review():
    row = one_measure(
        csv_document(),
        value_original="17",
        evidence=cited("17", "запись 2, столбец age (years)"),
    )

    [item] = row["content"]["evidence"]
    assert item["verification_status"] == "needs_review"
    assert item["locator"]["record"] == 1


def test_large_csv_without_records_gives_no_address():
    raw = source(
        version_id=CSV_ID, format="csv", content={"header": ["animal", "age"], "records": 3000}
    )

    row = one_measure(raw, value_original="19", evidence=cited("19", "запись 2, столбец age"))

    assert row["content"]["evidence"][0]["locator"] is None
    assert row["verification_status"] == "needs_review"


# ---------- Карточка ----------


def test_card_statements_and_context_are_located_too():
    candidate = verified(
        jats_document(),
        subjects=[{"text": "Колония", "evidence": cited("Captive colony of 12 animals", "§1")}],
        measures=[
            measure(evidence=cited("lived 31 years", "§2"), context_evidence=cited("absent"))
        ],
        set_aside=[{"kind": "other", "description": "x", "evidence": cited("wild 9 17")}],
    )

    [subject] = candidate.content["subjects"][0]["evidence"]
    assert subject["locator"]["id"] == "p1"
    assert subject["verification_status"] == "verified"
    [context] = candidate.measures[0]["content"]["context_evidence"]
    assert (context["locator"], context["verification_status"]) == (None, "needs_review")
    assert candidate.content["set_aside"][0]["evidence"][0]["locator"]["row"] == 3


def test_unparsed_document_gives_no_address():
    row = one_measure(source(version_id=PDF_ID, content=None))

    [item] = row["content"]["evidence"]
    assert (item["locator"], item["verification_status"]) == (None, "needs_review")
    assert item["diagnostic"] == "Содержимое документа не разобрано."


# ---------- Фрагмент для показа (#17) ----------


def shown(document, **fields) -> list[dict] | None:
    row = one_measure(document, **fields)
    return evidence.fragment(row["content"]["evidence"][0], document.content)


def test_pdf_fragment_is_the_page_text_around_the_quote():
    lines = [
        "Title",
        "Methods.",
        "a",
        "b",
        "The maximum lifespan of",
        "31 years was recorded.",
        "c",
    ]
    document = pages("Intro.", "\n".join(lines))

    fragment = shown(document, evidence=cited("maximum lifespan of 31 years", "страница 2"))

    assert fragment == [
        {"number": n, "text": lines[n - 1], "highlighted": n in (5, 6)} for n in range(3, 8)
    ]


def test_pdf_fragment_of_a_quote_across_a_page_break_ends_its_first_page():
    document = pages("Methods.\nThe oldest male was", "recaptured at 41 years of age.")

    fragment = shown(
        document,
        value_original="41",
        evidence=cited("The oldest male was recaptured at 41 years", "страница 1"),
    )

    assert [(line["text"], line["highlighted"]) for line in fragment] == [
        ("Methods.", False),
        ("The oldest male was", True),
    ]


def test_jats_fragment_is_the_sentence_of_the_node():
    fragment = shown(jats_document(), evidence=cited("The oldest male lived 31 years", "§2"))

    assert fragment == [
        {"number": 1, "text": "The oldest male lived 31 years in captivity.", "highlighted": True}
    ]


def test_jats_table_fragment_is_the_header_and_the_value_row():
    fragment = shown(
        jats_document(),
        measure_kind="median",
        definition="median lifespan",
        value_original="22",
        evidence=cited(
            "captive 22 31", "Table 1. Lifespan by group строка 2, столбец Median (years)"
        ),
    )

    assert fragment == [
        {"number": 1, "text": "Group | Median (years) | Maximum (years)", "highlighted": False},
        {"number": 2, "text": "captive | 22 | 31", "highlighted": True},
    ]


def test_csv_fragment_is_the_header_and_the_record():
    fragment = shown(
        csv_document(),
        value_original="19",
        evidence=cited("A2 short 19", "запись 2, столбец age (years)"),
    )

    assert fragment == [
        {"number": 0, "text": "animal | note | age (years)", "highlighted": False},
        {"number": 2, "text": "A2 | short | 19", "highlighted": True},
    ]


def test_quote_without_an_address_has_no_fragment():
    assert shown(pages("Nothing here."), evidence=cited("maximum lifespan of 31 years")) is None
