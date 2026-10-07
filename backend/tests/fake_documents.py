"""Контролируемые файлы документов: PDF с текстовым слоем, JATS Europe PMC, архив приложений.

Полные тексты корпуса не хранятся в Git (data/corpus/README), поэтому тесты собирают малые
файлы с нужными рисками: обложка, чужой DOI в литературе, страница без текста.
"""

import io
import json
import zipfile

EPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL = "https://api.unpaywall.org/v2"


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf(*pages: str) -> bytes:
    """PDF с текстовым слоем; пустая строка — страница без текста, как у скана."""
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    kids = []
    number = 4
    for text in pages:
        lines = " ".join(f"({_escape(line)}) Tj T*" for line in text.split("\n"))
        stream = f"BT /F1 10 Tf 12 TL 40 760 Td {lines} ET".encode() if text else b""
        page, content = number, number + 1
        number += 2
        objects[page] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content
        )
        objects[content] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        kids.append(page)
    refs = " ".join(f"{kid} 0 R" for kid in kids).encode()
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (refs, len(kids))
    out = b"%PDF-1.4\n"
    offsets = {}
    for key in range(1, number):
        offsets[key] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (key, objects[key])
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % number
    out += b"".join(b"%010d 00000 n \n" % offsets[key] for key in range(1, number))
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (number, xref)
    return out


def jats(
    *,
    doi: str,
    pmcid: str,
    title: str = "Naked mole-rat mortality rates defy Gompertzian laws",
    supplements: tuple[str, ...] = (),
    body: bool = True,
) -> bytes:
    listed = "".join(
        f'<supplementary-material id="supp{n}"><label>Supplementary file {n}.</label>'
        f'<media xlink:href="{name}" mimetype="text"/></supplementary-material>'
        for n, name in enumerate(supplements, 1)
    )
    text = (
        '<body><sec id="s1"><title>Results</title><p id="p1">Hazard did not increase.</p>'
        '<table-wrap id="t1"><label>Table 1.</label><table><tr><th>Age</th></tr>'
        f"<tr><td>30</td></tr></table></table-wrap>{listed}</sec></body>"
        if body
        else ""
    )
    return (
        '<?xml version="1.0"?><article xmlns:xlink="http://www.w3.org/1999/xlink">'
        "<front><article-meta>"
        f'<article-id pub-id-type="pmcid">{pmcid}</article-id>'
        f'<article-id pub-id-type="doi">{doi}</article-id>'
        f"<title-group><article-title>{title}</article-title></title-group>"
        f"</article-meta></front>{text}"
        # Список литературы ссылается на чужие работы: это не идентификаторы статьи.
        '<back><ref-list><ref><pub-id pub-id-type="doi">10.1000/other</pub-id></ref></ref-list>'
        "</back></article>"
    ).encode()


def archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, data in files.items():
            zipped.writestr(name, data)
    return buffer.getvalue()


def unpaywall(*pdf_urls: str) -> bytes:
    locations = [{"url_for_pdf": url} for url in pdf_urls]
    return json.dumps(
        {"best_oa_location": locations[0] if locations else None, "oa_locations": locations}
    ).encode()
