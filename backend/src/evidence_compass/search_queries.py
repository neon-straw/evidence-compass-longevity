"""Native renderers adapted from evidence-compass-small-pilot/search_plan_render.py.

Confirmed CriteriaFields already form our neutral plan. Rendering does not call an LLM
or discard terms. Query length is checked separately before confirmation and start,
so even an old invalid version can still be previewed without a server error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evidence_compass.criteria import CriteriaFields, Resource

# Application guard, not a claimed universal limit of either scientific source.
MAX_QUERY_LEN = 2000
EARLIEST_YEAR = 1800
LATEST_YEAR = 3000


@dataclass(frozen=True)
class Vocabulary:
    terms: dict[str, str]
    rest: str | None = None


def _any(terms: list[str]) -> str:
    return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"


def _text(term: str, resource: Resource) -> str:
    phrase = f'"{term}"' if " " in term or "-" in term else term
    return f"{phrase}[tiab]" if resource == "pubmed" else f"TITLE_ABS:{phrase}"


def _choice(selected: list[str], vocabulary: Vocabulary) -> tuple[str, bool]:
    all_keys = set(vocabulary.terms)
    if vocabulary.rest:
        all_keys.add(vocabulary.rest)
    if set(selected) == all_keys:
        return "", False
    if vocabulary.rest and vocabulary.rest in selected:
        return _any([value for key, value in vocabulary.terms.items() if key not in selected]), True
    return _any([vocabulary.terms[key] for key in selected]), False


PUBLICATION_KINDS = {
    "pubmed": Vocabulary(
        {"journal_article": "journal article[pt]", "preprint": "preprint[Filter]"}, rest="other"
    ),
    "europe_pmc": Vocabulary(
        {"journal_article": "(SRC:MED OR SRC:PMC)", "preprint": "SRC:PPR"}, rest="other"
    ),
}
ARTICLE_TYPES = {
    "pubmed": Vocabulary(
        {
            "review": "review[pt]",
            "meta_analysis": "meta-analysis[pt]",
            "other": "(editorial[pt] OR letter[pt] OR comment[pt] OR news[pt])",
        },
        rest="primary_study",
    ),
    "europe_pmc": Vocabulary(
        {
            "review": 'PUB_TYPE:"review"',
            "meta_analysis": 'PUB_TYPE:"meta-analysis"',
            "other": '(PUB_TYPE:"editorial" OR PUB_TYPE:"letter" '
            'OR PUB_TYPE:"comment" OR PUB_TYPE:"news")',
        },
        rest="primary_study",
    ),
}
CONDITIONS = {
    "wild": ["wild", "free-living", "free-ranging"],
    "captive": ["captive", "captivity", "zoo", "laboratory"],
}


def _render(fields: CriteriaFields, resource: Resource) -> str:
    groups = [[term for item in fields.objects for term in [item.scientific_name, *item.aliases]]]
    groups += [criterion.terms for criterion in fields.search_criteria]
    included: list[str] = []
    seen: set[frozenset[str]] = set()
    for group in groups:
        unique: dict[str, str] = {}
        for term in group:
            if stripped := term.strip():
                unique.setdefault(stripped.casefold(), stripped)
        terms = list(unique.values())
        key = frozenset(term.casefold() for term in terms)
        if terms and key not in seen:
            seen.add(key)
            included.append(_any([_text(term, resource) for term in terms]))
    excluded: list[str] = []
    conditions = Vocabulary(
        {key: _any([_text(term, resource) for term in terms]) for key, terms in CONDITIONS.items()}
    )
    for selected, vocabulary in (
        (fields.conditions, conditions),
        (fields.publication_kinds, PUBLICATION_KINDS[resource]),
        (fields.article_types, ARTICLE_TYPES[resource]),
    ):
        # Invalid drafts are explained by criteria.problems, never silently confirmed.
        if not selected:
            continue
        clause, negated = _choice(list(selected), vocabulary)
        if clause:
            (excluded if negated else included).append(clause)
    period = fields.period
    if period.from_year or period.to_year:
        start, end = period.from_year or EARLIEST_YEAR, period.to_year or LATEST_YEAR
        included.append(
            f"{start}:{end}[dp]" if resource == "pubmed" else f"PUB_YEAR:[{start} TO {end}]"
        )
    return " AND ".join(included) + "".join(f" NOT {clause}" for clause in excluded)


def render_pubmed_query(fields: CriteriaFields) -> str:
    return _render(fields, "pubmed")


def render_europe_pmc_query(fields: CriteriaFields) -> str:
    return _render(fields, "europe_pmc")


def query(resource: Resource, fields: CriteriaFields) -> str:
    render = render_pubmed_query if resource == "pubmed" else render_europe_pmc_query
    return render(fields)
