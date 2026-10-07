"""Строит демо-строки результата из data/corpus/expected-results.csv.

Запуск: python3 scripts/corpus_measures.py > src/mocks/data/measures.json
Значения переносятся как есть; строки остаются кандидатными (unreviewed) до сверки пользователем.
"""

import csv
import json
import sys
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "data" / "corpus"

KIND = {
    "modelled biological age": "modelled_age",
    "median lifespan": "median",
    "model-implied median lifespan": "median",
    "latest recorded death age": "observed_maximum",
    "estimated age at sexual maturity": "maturity_age",
    "elapsed time from banding to live recapture; field longevity record": "survival_interval",
}
LABEL = {
    "nielsen2016": "Nielsen 2016",
    "clubb2008": "Clubb 2008",
    "ruby2018": "Ruby 2018",
    "podlutsky2005": "Podlutsky 2005",
}
DOCUMENT = {
    "shark-complete": ("shark-complete.pdf", 1),
    "elephant-complete": ("elephant-published.pdf", 1),
    "ruby-main": ("ruby2018.pdf", 2),
    "bat-main": ("podlutsky2005.pdf", 1),
}


# Историческое название сохраняется; оговорка о таксономии — из оговорок корпуса.
TAXONOMY_NOTE = {
    "podlutsky2005": "Историческое название из публикации; современная таксономическая "
    "принадлежность этой особи не проверена.",
}


def setting(conditions: str) -> str:
    text = conditions.lower()
    if "captiv" in text or "zoo" in text:
        return "captive"
    return "wild" if "wild" in text else "unknown"


def uncertainty(r: dict) -> dict | None:
    kind = r["uncertainty_type"]
    if not kind or kind == "not reported":
        return None
    level = r["uncertainty_level_percent"]
    if kind == "confidence interval":
        label = f"{level}% ДИ" if level else "ДИ"
    elif "posterior" in kind:
        label = f"{level}% апостериорный интервал" if level else "апостериорный интервал"
    else:
        label = kind
    return {
        "label": label,
        "kind": kind,
        "level_percent": float(level) if level else None,
        "plus_minus": r["uncertainty_plus_minus"] or None,
        "lower": r["uncertainty_lower"] or None,
        "upper": r["uncertainty_upper"] or None,
        "upper_status": r["uncertainty_upper_status"] or None,
    }


def main() -> None:
    rows = list(csv.DictReader(open(CORPUS / "expected-results.csv", encoding="utf-8")))
    measures = []
    for n, r in enumerate(rows, 1):
        name, version = DOCUMENT[r["material_id"]]
        measures.append(
            {
                "row": {
                    "id": r["result_id"],
                    "number": n,
                    "species_name_in_source": r["species_in_source"],
                    "species_label": r["species_in_source"],
                    "taxonomy_note": TAXONOMY_NOTE.get(r["publication_id"]),
                    "measure_kind": KIND.get(r["measure"], "other"),
                    "definition": r["measure"],
                    "value_original": r["value_original"],
                    "unit_original": r["unit"],
                    "qualifier": r["qualifier"] or None,
                    "uncertainty": uncertainty(r),
                    "subject": {
                        "label": r["subject"],
                        "sex": r["sex"] or None,
                        "sample_size": r["sample_size"] or None,
                        "sample_size_scope": r["sample_size_scope"] or None,
                    },
                    "conditions": {"setting": setting(r["conditions"]), "text": r["conditions"]},
                    "method": r["method"] or None,
                    "publication_id": r["publication_id"],
                    "publication_label": LABEL[r["publication_id"]],
                    "author_notes": r["caveat"] or None,
                    "origin_kind": "own_result",
                    "evidence_ids": [f"{r['result_id']}-value"],
                    "context_evidence_ids": (
                        [f"{r['result_id']}-context"] if r["context_location"] else []
                    ),
                    "related_measure_ids": [x for x in r["related_result_id"].split(";") if x],
                    "review_status": "unreviewed",
                    "verification_status": "needs_review",
                },
                "evidence": {
                    "document_name": name,
                    "document_version": version,
                    "page": int(r["pdf_page"]) if r["pdf_page"] else None,
                    "printed": r["printed_location"],
                    "context": r["context_location"] or None,
                    "doi": r["doi"],
                },
            }
        )
    json.dump(measures, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
