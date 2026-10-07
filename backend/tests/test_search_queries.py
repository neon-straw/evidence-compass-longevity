"""Native query rendering, adapted from the small pilot's golden cases."""

import pytest

from evidence_compass.criteria import DEFAULT_FIELDS, SearchCriterion, StudyObject, problems
from evidence_compass.search_queries import query


def criteria(*terms: str):
    fields = DEFAULT_FIELDS.model_copy(deep=True)
    fields.publication_kinds = ["journal_article", "preprint", "other"]
    fields.search_criteria = [
        SearchCriterion(
            id="lifespan", label="Возраст", terms=list(terms), suggested_terms=[], origin="added"
        )
    ]
    return fields


@pytest.mark.parametrize(
    "species",
    ["Heterocephalus glaber", "Somniosus microcephalus", "Myotis brandtii", "Loxodonta africana"],
)
def test_native_fields_for_corpus_species(species):
    fields = criteria("lifespan", "maximum longevity")
    fields.objects = [
        StudyObject(id="animal", name=species, scientific_name=species, origin="added")
    ]
    assert (
        query("pubmed", fields)
        == f'"{species}"[tiab] AND (lifespan[tiab] OR "maximum longevity"[tiab])'
    )
    assert (
        query("europe_pmc", fields)
        == f'TITLE_ABS:"{species}" AND (TITLE_ABS:lifespan OR TITLE_ABS:"maximum longevity")'
    )


def test_hyphens_conditions_and_preprint_exclusion():
    fields = criteria("free-living")
    fields.conditions = ["wild"]
    fields.publication_kinds = ["journal_article", "other"]
    assert (
        query("pubmed", fields)
        == '"free-living"[tiab] AND (wild[tiab] OR "free-living"[tiab] OR "free-ranging"[tiab])'
        " NOT preprint[Filter]"
    )
    assert (
        query("europe_pmc", fields)
        == 'TITLE_ABS:"free-living" AND (TITLE_ABS:wild OR TITLE_ABS:"free-living"'
        ' OR TITLE_ABS:"free-ranging") NOT SRC:PPR'
    )


def test_query_length_limit_includes_final_filters():
    fields = criteria("x" * 1994)
    fields.resources = ["pubmed"]
    assert len(query("pubmed", fields)) == 2000
    assert problems(fields) == []
    fields.search_criteria[0].terms[0] += "x"
    assert any(
        "2000" in problem.message and "PubMed" in problem.message for problem in problems(fields)
    )


@pytest.mark.parametrize(
    "term", ["foo[tiab]", "foo:bar", 'foo"bar', "foo*", "foo OR bar", "foo\\bar"]
)
def test_species_cannot_inject_query_syntax(term):
    fields = criteria("lifespan")
    fields.objects = [StudyObject(id="animal", name="Animal", scientific_name=term, origin="added")]
    assert any(problem.target == "animal" for problem in problems(fields))


@pytest.mark.parametrize(
    "start, end, pm, epmc",
    [
        (2000, None, "2000:3000[dp]", "PUB_YEAR:[2000 TO 3000]"),
        (None, 2010, "1800:2010[dp]", "PUB_YEAR:[1800 TO 2010]"),
        (2000, 2010, "2000:2010[dp]", "PUB_YEAR:[2000 TO 2010]"),
    ],
)
def test_dates_keep_source_specific_syntax(start, end, pm, epmc):
    fields = criteria("longevity")
    fields.period.from_year, fields.period.to_year = start, end
    assert query("pubmed", fields) == f"longevity[tiab] AND {pm}"
    assert query("europe_pmc", fields) == f"TITLE_ABS:longevity AND {epmc}"


def test_length_validation_counts_filters_and_only_selected_resources():
    fields = criteria("x" * 1985)
    fields.resources = ["pubmed"]
    assert problems(fields) == []
    fields.publication_kinds = ["preprint"]
    assert len(query("pubmed", fields)) > 2000
    assert any("2000" in problem.message for problem in problems(fields))


@pytest.mark.parametrize(
    "resource, field", [("pubmed", "preprint[Filter]"), ("europe_pmc", "SRC:PPR")]
)
def test_preprints_only_and_other_publication_kinds(resource, field):
    fields = criteria("longevity")
    fields.publication_kinds = ["preprint"]
    assert query(resource, fields).endswith(f" AND {field}")
    fields.publication_kinds = ["other"]
    assert f"OR {field})" in query(resource, fields)
    assert " NOT " in query(resource, fields)


def test_more_than_pilot_term_limit_is_not_silently_truncated():
    fields = criteria(*(f"term{i}" for i in range(25)))
    assert problems(fields) == []
    assert "term24[tiab]" in query("pubmed", fields)
    assert "TITLE_ABS:term24" in query("europe_pmc", fields)


def test_aliases_expand_one_species_group_without_requiring_latin_names():
    fields = criteria("lifespan", "longevity")
    fields.objects = [
        StudyObject.model_validate(
            {
                "id": "rat",
                "name": "Землекоп",
                "scientific_name": "Heterocephalus glaber",
                "aliases": ["naked mole rat", " NAKED MOLE RAT ", "Heterocephalus glaber"],
                "origin": "added",
            }
        ),
        StudyObject.model_validate(
            {
                "id": "shark",
                "name": "Акула",
                "scientific_name": "Somniosus microcephalus",
                "aliases": ["Greenland shark"],
                "origin": "added",
            }
        ),
    ]
    assert query("pubmed", fields) == (
        '("Heterocephalus glaber"[tiab] OR "naked mole rat"[tiab] OR '
        '"Somniosus microcephalus"[tiab] OR "Greenland shark"[tiab])'
        " AND (lifespan[tiab] OR longevity[tiab])"
    )
    assert query("europe_pmc", fields) == (
        '(TITLE_ABS:"Heterocephalus glaber" OR TITLE_ABS:"naked mole rat" OR '
        'TITLE_ABS:"Somniosus microcephalus" OR TITLE_ABS:"Greenland shark")'
        " AND (TITLE_ABS:lifespan OR TITLE_ABS:longevity)"
    )


def test_elephant_alias_is_an_alternative_not_an_additional_requirement():
    fields = criteria("survivorship")
    fields.objects = [
        StudyObject.model_validate(
            {
                "id": "elephant",
                "name": "Слон",
                "scientific_name": "Loxodonta africana",
                "aliases": ["African elephants"],
                "origin": "added",
            }
        )
    ]
    assert query("pubmed", fields) == (
        '("Loxodonta africana"[tiab] OR "African elephants"[tiab]) AND survivorship[tiab]'
    )


@pytest.mark.parametrize("alias", ["foo[tiab]", "foo OR bar", "foo*", "foo:bar"])
def test_alias_syntax_blocks_confirmation(alias):
    fields = criteria("lifespan")
    fields.objects = [
        StudyObject.model_validate(
            {
                "id": "rat",
                "name": "Землекоп",
                "scientific_name": "Heterocephalus glaber",
                "aliases": [alias],
                "origin": "added",
            }
        )
    ]
    assert any(p.target == "rat" for p in problems(fields))


def test_aliases_count_toward_query_length_limit():
    fields = criteria("lifespan")
    fields.objects = [
        StudyObject.model_validate(
            {
                "id": "rat",
                "name": "Землекоп",
                "scientific_name": "Heterocephalus glaber",
                "aliases": ["x" * 2000],
                "origin": "added",
            }
        )
    ]
    assert any("2000" in p.message for p in problems(fields))
