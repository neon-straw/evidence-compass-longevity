"""Локальные проверки разбора для рисков адресации, которые не видны через API (spec)."""

from evidence_compass.parsing import SMALL_CSV, parse


def test_csv_record_is_not_a_physical_line():
    parsed = parse(b'id,note\nA1,"first line\nsecond line"\nA2,short\n', "animals.csv")

    assert parsed.status == "parsed"
    assert parsed.content["header"] == ["id", "note"]
    # Три физические строки данных, но две записи: адрес CSV — номер записи.
    assert parsed.content["records"] == 2
    assert parsed.content["dialect"]["delimiter"] == ","
    # Малый файл хранит записи целиком: по ним строится адрес ячейки основания.
    assert parsed.content["rows"] == [["A1", "first line\nsecond line"], ["A2", "short"]]


def test_large_csv_keeps_only_its_header():
    lines = "".join(f"A{number},{number}\n" for number in range(SMALL_CSV + 1))

    parsed = parse(f"id,age\n{lines}".encode(), "raw.csv")

    assert parsed.content["records"] == SMALL_CSV + 1
    assert "rows" not in parsed.content


def test_jats_keeps_node_paths_and_table_cells():
    xml = (
        b'<article><front><article-meta><article-id pub-id-type="doi">10.1/a</article-id>'
        b"</article-meta></front><body><sec><p>One.</p><p>Two.</p></sec>"
        b'<sec><table-wrap id="t2"><table><tr><th>Age</th><th>Age</th></tr>'
        b"<tr><td>30</td><td>31</td></tr></table></table-wrap></sec></body></article>"
    )

    parsed = parse(xml, "a.xml")

    assert [block["path"] for block in parsed.content["blocks"]] == [
        "/article/body[1]/sec[1]/p[1]",
        "/article/body[1]/sec[1]/p[2]",
    ]
    (table,) = parsed.content["tables"]
    # Повторяющиеся заголовки столбцов различаются позицией ячейки.
    assert (table["id"], table["rows"]) == ("t2", [["Age", "Age"], ["30", "31"]])
