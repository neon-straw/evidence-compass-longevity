"""Ревизия справки: краткий вывод, охват и таблица показателей одной версии (#17; spec «Справка
и интерфейс», «Сущности и связи», AC10–11).

`ReportRevision` закрепляет точные версии карточек и строк: таблица читается только из них, а
не из последних ревизий карточек. Ревизия записывается, когда меняются её входы — ревизии
карточек выбранных работ, итоги их обработки и охват; иначе чтение возвращает прежнюю.

Краткий вывод собирается шаблоном из допустимых строк: сначала находки со ссылками на строки,
затем пределы вывода. Он не называет причин долголетия, не выбирает лучшую оценку и не
оценивает качество исследований. Без допустимых строк содержательного вывода нет — только
охват и причины.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from evidence_compass import documents, evidence, extraction, questions, search, selection
from evidence_compass.cards import CardRevisionRecord, MeasureRecord
from evidence_compass.criteria import CriteriaVersionRecord
from evidence_compass.db import Base
from evidence_compass.documents import DocumentRecord, DocumentVersionRecord
from evidence_compass.selection import PublicationRecord

# Тип показателя словами: среднее, медиана и максимум не взаимозаменяемы.
KIND_NAMES = {
    "mean": "среднее",
    "median": "медиана",
    "observed_maximum": "наблюдаемый максимум",
    "individual_age": "возраст особи",
    "modelled_age": "модельная оценка возраста",
    "survival_interval": "интервал выживания",
    "maturity_age": "возраст созревания",
    "other": "другой показатель",
}
PUBLICATION_KINDS = {"journal_article": "статьи", "preprint": "препринты", "other": "другие"}
CONDITIONS = {"wild": "природа", "captive": "неволя"}
SETTINGS = {**CONDITIONS, "unknown": "условия не указаны"}
RESOURCES = {"pubmed": "PubMed", "europe_pmc": "Europe PMC", "manual": "добавлена вручную"}
VERSION_TYPES = {"journal": "журнальная статья", "preprint": "препринт"}
# После «из»: из 1 строки, из 3 строк.
OF_ROWS = ("строки", "строк", "строк")

DIFFERENT_DEFINITIONS = (
    "Показатели имеют разные определения и условия и не сводятся к одному значению вида."
)
NOT_REVIEWED = (
    "строки ещё не проверены пользователем: это предварительная выборка, а не научное подтверждение"
)
NO_MEASURES = (
    "Нет допустимых строк: обработанные работы не дали опубликованных показателей. "
    "Причины — в охвате."
)
# Версия шаблона краткого вывода входит во входы ревизии: новый шаблон — новая ревизия,
# прежняя остаётся как была.
SUMMARY_TEMPLATE = 6
NOT_PROCESSED = "не обработана"
PROCESSING = "обрабатывается"
# Почему строка не вошла в краткий вывод; порядок — приоритет причины.
EXCLUSIONS = {
    "rejected": "отклонены при проверке извлечения",
    "secondary_report": "не собственный результат работы: значение приведено по другим работам "
    "или как известный факт, первоисточник не проверен",
    "unresolved": "в источнике расхождение или неоднозначность, нужно решение",
    "needs_review": "число с единицами не найдено в тексте документа по цитате, "
    "нужна сверка с документом; после сверки отметьте строку проверенной",
}
# «Строка 03 — сверена …», «Строки 03, 05 — сверены …».
CHECKED_MANUALLY_VERB = ("сверена", "сверены")


class ReportRevisionRecord(Base):
    """Неизменяемая ревизия справки исследования."""

    __tablename__ = "report_revisions"
    __table_args__ = (UniqueConstraint("study_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"))
    number: Mapped[int]
    # Ревизия, по которой построен краткий вывод; меньше `number` — вывод устарел.
    summary_revision: Mapped[int]
    stale_reason: Mapped[str | None] = mapped_column(Text)
    # Входы, по которым построена ревизия: изменились — нужна новая.
    inputs: Mapped[dict] = mapped_column(JSONB)
    # Вопрос, параметры поиска, охват и краткий вывод на момент ревизии.
    content: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportCardRevisionRecord(Base):
    """Ревизия карточки, использованная справкой: удалить её нельзя."""

    __tablename__ = "report_card_revisions"

    report_revision_id: Mapped[int] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    card_revision_id: Mapped[int] = mapped_column(
        ForeignKey(CardRevisionRecord.id), primary_key=True
    )


class ReportMeasureRecord(Base):
    """Версия показателя — строка таблицы ревизии справки со своим номером."""

    __tablename__ = "report_measures"
    __table_args__ = (UniqueConstraint("report_revision_id", "number"),)

    report_revision_id: Mapped[int] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="CASCADE"), primary_key=True
    )
    measure_id: Mapped[int] = mapped_column(ForeignKey(MeasureRecord.id), primary_key=True)
    number: Mapped[int]


# ---------- Входы ----------


async def _latest_card(db: AsyncSession, publication_id: int) -> CardRevisionRecord | None:
    """Последняя ревизия карточки: ошибка следующей попытки её не убирает."""
    return await db.scalar(
        select(CardRevisionRecord)
        .where(CardRevisionRecord.publication_id == publication_id)
        .order_by(CardRevisionRecord.number.desc())
        .limit(1)
    )


async def _inputs(db: AsyncSession, account_id: int, study_id: int) -> dict | None:
    """Работы выбора с их ревизией карточки, итогом и документами; None — обработки не было."""
    found = await documents.get(db, account_id)
    listed = await selection.get(db, account_id)
    assert found is not None
    latest = await extraction.latest_items(db, study_id)
    works = []
    for work in found.works:
        item = latest.get(work.publication_id)
        revision = await _latest_card(db, work.publication_id)
        works.append(
            {
                "publication_id": work.publication_id,
                "label": work.label,
                "card_revision_id": revision.id if revision else None,
                "execution": item.execution if item else None,
                "outcome": item.outcome if item else None,
                "detail": item.detail if item else None,
                "documents": work.state,
                "gaps": work.gaps,
                "unresolved": [
                    {
                        "description": entry["description"],
                        "measure_numbers": entry["measure_numbers"],
                    }
                    for entry in revision.content["unresolved"]
                ]
                if revision
                else [],
                "set_aside": [
                    {"kind": entry["kind"], "description": entry["description"]}
                    for entry in revision.content["set_aside"]
                ]
                if revision
                else [],
            }
        )
    if not any(work["execution"] for work in works):
        return None
    return {
        "summary_template": SUMMARY_TEMPLATE,
        "found": len(listed.publications) if listed else 0,
        "works": works,
    }


# ---------- Строки ----------


@dataclass(frozen=True)
class Row:
    """Строка таблицы: версия показателя в закреплённой ревизии карточки."""

    number: int
    measure: MeasureRecord
    revision: CardRevisionRecord
    publication: PublicationRecord


def _evidence_id(measure_id: int, role: str, n: int) -> str:
    """ID основания строки: отдельной таблицы оснований нет, их порядок в версии неизменен."""
    return f"{measure_id}-{role}-{n}"


def _ids(measure_id: int, role: str, items: list) -> list[str]:
    return [_evidence_id(measure_id, role, n) for n in range(1, len(items) + 1)]


def _unresolved(revision: CardRevisionRecord) -> set[int]:
    return {n for item in revision.content["unresolved"] for n in item["measure_numbers"]}


def _checked_manually(row: Row) -> bool:
    """Автоматическая сверка не прошла, но пользователь сверил строку с документом и отметил
    проверенной. Исправленная строка (`corrected`) требует повторной проверки и не в счёт."""
    measure = row.measure
    return measure.verification_status != "verified" and measure.review_status == "checked"


def _exclusion(row: Row) -> str | None:
    """Причина, по которой строка не входит в краткий вывод; None — допустимая строка."""
    measure = row.measure
    if measure.review_status == "rejected":
        return "rejected"
    if measure.origin_kind == "secondary_report":
        return "secondary_report"
    if measure.number in _unresolved(row.revision):
        return "unresolved"
    if measure.verification_status != "verified" and not _checked_manually(row):
        return "needs_review"
    return None


def _verification_gap(measure: MeasureRecord) -> str | None:
    """Почему сверка не прошла: цитата найдена по адресу, но значение в ней не совпало, или
    цитаты в документе не нашлось."""
    if measure.verification_status == "verified":
        return None
    if any(item.get("locator") for item in measure.content["evidence"]):
        return "value_not_matched"
    return "quote_not_found"


def _measure_row(row: Row, siblings: dict[int, int]) -> dict:
    """Строка таблицы в форме API; `siblings` — номер показателя в карточке → его ID."""
    measure, content = row.measure, row.measure.content
    limitations = {
        item["number"]: item["text"] for item in row.revision.content["author_limitations"]
    }
    notes = [limitations[n] for n in content["author_limitation_numbers"] if n in limitations]
    return {
        "id": str(measure.id),
        "number": row.number,
        "species_name_in_source": content["species_name_in_source"],
        "species_label": content["species_label"],
        "taxonomy_note": content["taxonomy_note"],
        "measure_kind": measure.measure_kind,
        "definition": content["definition"],
        "value_original": content["value_original"],
        "unit_original": content["unit_original"],
        "qualifier": content["qualifier"],
        "uncertainty": content["uncertainty"],
        "subject": {
            key: content["subject"][key]
            for key in ("label", "sex", "sample_size", "sample_size_scope")
        },
        "conditions": content["conditions"],
        "method": content["method"],
        "publication_id": str(row.publication.id),
        "publication_label": documents.label(row.publication),
        "author_notes": "; ".join(notes) or None,
        "origin_kind": measure.origin_kind,
        "evidence_ids": _ids(measure.id, "value", content["evidence"]),
        "context_evidence_ids": _ids(measure.id, "context", content["context_evidence"]),
        "related_measure_ids": [
            str(siblings[n]) for n in content["related_measure_numbers"] if n in siblings
        ],
        "review_status": measure.review_status,
        "verification_status": measure.verification_status,
        "verification_gap": _verification_gap(measure),
    }


async def _card_rows(db: AsyncSession, card_revision_ids: list[int]) -> list[Row]:
    rows = []
    for revision_id in card_revision_ids:
        revision = await db.get(CardRevisionRecord, revision_id)
        assert revision is not None
        publication = await db.get(PublicationRecord, revision.publication_id)
        assert publication is not None
        measures = await db.scalars(
            select(MeasureRecord)
            .where(MeasureRecord.revision_id == revision_id)
            .order_by(MeasureRecord.number)
        )
        rows.extend(Row(len(rows) + 1, measure, revision, publication) for measure in measures)
    return rows


async def _pinned_rows(db: AsyncSession, report: ReportRevisionRecord) -> list[Row]:
    links = await db.scalars(
        select(ReportMeasureRecord)
        .where(ReportMeasureRecord.report_revision_id == report.id)
        .order_by(ReportMeasureRecord.number)
    )
    rows = []
    for link in links:
        measure = await db.get(MeasureRecord, link.measure_id)
        assert measure is not None
        revision = await db.get(CardRevisionRecord, measure.revision_id)
        assert revision is not None
        publication = await db.get(PublicationRecord, revision.publication_id)
        assert publication is not None
        rows.append(Row(link.number, measure, revision, publication))
    return rows


def _table(rows: list[Row]) -> list[dict]:
    siblings: dict[int, dict[int, int]] = {}
    for row in rows:
        siblings.setdefault(row.revision.id, {})[row.measure.number] = row.measure.id
    return [_measure_row(row, siblings[row.revision.id]) for row in rows]


# ---------- Краткий вывод и охват ----------


def _span(numbers: list[int]) -> str:
    """«Строка 03», «Строки 01–04», «Строки 01, 03»."""
    numbers = sorted(numbers)
    if len(numbers) == 1:
        return f"Строка {numbers[0]:02d}"
    if numbers == list(range(numbers[0], numbers[-1] + 1)):
        return f"Строки {numbers[0]:02d}–{numbers[-1]:02d}"
    return "Строки " + ", ".join(f"{n:02d}" for n in numbers)


def _short(label: str) -> str:
    """Автор и год вместо полного названия работы."""
    return label.split(" · ", 1)[0].rstrip(".")


def _species(content: dict) -> str:
    """Подпись вида из критериев; исходное название источника не теряется."""
    label, name = content["species_label"], content["species_name_in_source"]
    label = label[:1].upper() + label[1:]
    return label if name.casefold() in label.casefold() else f"{label} (в источнике — {name})"


def _finding_item(row: Row) -> str:
    """Показатель как опубликован: тип, значение с оговоркой, единицами и неопределённостью,
    особь или группа и условия."""
    content = row.measure.content
    value = " ".join(
        filter(None, [content["qualifier"], content["value_original"], content["unit_original"]])
    )
    if content["uncertainty"]:
        value += f" ({content['uncertainty']['label']})"
    context = ", ".join(
        [f"«{content['subject']['label']}»", SETTINGS[content["conditions"]["setting"]]]
    )
    return f"{KIND_NAMES[row.measure.measure_kind]} — {value}, {context} (строка {row.number:02d})"


def _structured_finding(row: Row) -> dict:
    content = row.measure.content
    value = " ".join(
        filter(None, [content["qualifier"], content["value_original"], content["unit_original"]])
    )
    if uncertainty := content["uncertainty"]:
        details = []
        if uncertainty["plus_minus"] is not None:
            details.append(f"± {uncertainty['plus_minus']} {content['unit_original']}")
        if uncertainty["lower"] is not None or uncertainty["upper"] is not None:
            upper = uncertainty["upper"] or (
                "не достигнута" if uncertainty["upper_status"] == "not_reached" else "не указана"
            )
            details.append(
                f"[{uncertainty['lower'] or 'не указана'}; {upper}] {content['unit_original']}"
            )
        details.append(uncertainty["label"])
        value += f" ({'; '.join(details)})"
    return {
        "measure_id": str(row.measure.id),
        "number": row.number,
        "title": f"{KIND_NAMES[row.measure.measure_kind].capitalize()} — {value}",
        "context": f"{SETTINGS[content['conditions']['setting']]} · {content['subject']['label']}",
        "publication": _short(documents.label(row.publication)),
        "checked_manually": _checked_manually(row),
    }


def _reason(work: dict) -> str | None:
    """Почему работа не дала строк или что в ней не прочитано."""
    if work["execution"] in extraction.UNFINISHED:
        reason: str | None = PROCESSING
    elif work["outcome"] is None:
        reason = NOT_PROCESSED
    elif work["outcome"] == "measures_extracted":
        reason = None
    else:
        reason = work["detail"] or extraction.OUTCOME_LABELS[work["outcome"]]
    parts = [part.rstrip(".") for part in [reason, *work["gaps"]] if part]
    return "; ".join(parts) or None


def _coverage(inputs: dict, rows: list[Row]) -> dict:
    works = inputs["works"]
    counts: dict[int, int] = {}
    for row in rows:
        counts[row.publication.id] = counts.get(row.publication.id, 0) + 1
    unavailable = sum(
        work["outcome"] == "unavailable" or work["documents"] == "no_full_text" for work in works
    )
    extracted = sum(work["outcome"] == "measures_extracted" for work in works)
    return {
        "found": inputs["found"],
        "selected": len(works),
        "full_texts": len(works) - unavailable,
        "unavailable": unavailable,
        "not_processed": sum(work["outcome"] is None for work in works),
        "share": extracted / len(works) if works else 0.0,
        "works": [
            {
                "publication_id": str(work["publication_id"]),
                "label": work["label"],
                "outcome": work["outcome"],
                "measure_count": counts.get(work["publication_id"], 0),
                "reason": _reason(work),
            }
            for work in works
        ],
    }


def _discrepancy(item: dict, publication_id: int, numbers: dict[tuple[int, int], int]) -> str:
    """Расхождение с номерами строк таблицы, к которым оно относится: «Строка 03 — …»."""
    description = item["description"].rstrip(".")
    rows = [
        numbers[key] for n in item["measure_numbers"] if (key := (publication_id, n)) in numbers
    ]
    return f"{_span(rows)} — {description}" if rows else description


def _absent_works(inputs: dict, rows: list[Row], eligible: list[Row]) -> list[dict]:
    """Работы, не давшие показателей в кратком выводе: итог обработки отдельно от причин —
    неразрешённых расхождений, отложенного из карточки и пробелов документов. Клиент рисует
    по строке на работу, а не склеенную фразу. Итог `unresolved` бывает и у работы с
    допустимыми строками — такая работа в выводе есть и в список не входит."""
    in_summary = {row.publication.id for row in eligible}
    numbers = {(row.publication.id, row.measure.number): row.number for row in rows}
    works = [
        {
            "publication_id": str(work["publication_id"]),
            "label": _short(work["label"]),
            "outcome": work["outcome"],
            "unresolved": [
                _discrepancy(item, work["publication_id"], numbers) for item in work["unresolved"]
            ],
            "set_aside": [
                {"kind": item["kind"], "description": item["description"].rstrip(".")}
                for item in work["set_aside"]
            ],
            "gaps": list(work["gaps"]),
        }
        for work in inputs["works"]
        if work["outcome"] != "measures_extracted" and work["publication_id"] not in in_summary
    ]
    # Работы, требующие решения пользователя, важнее справочных.
    works.sort(key=lambda w: w["outcome"] != "unresolved")
    return works


def _summary(inputs: dict, rows: list[Row]) -> dict:
    eligible = [row for row in rows if _exclusion(row) is None]
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in eligible:
        key = (row.measure.content["species_label"], row.measure.content["species_name_in_source"])
        groups.setdefault(key, []).append(row)
    findings = []
    for items in groups.values():
        descriptions = [
            f"{_short(documents.label(row.publication))}: {_finding_item(row)}" for row in items
        ]
        findings.append(
            {
                "text": f"{_species(items[0].measure.content)}, {'; '.join(descriptions)}.",
                "measure_refs": [str(row.measure.id) for row in items],
                "species": _species(items[0].measure.content),
                "items": [_structured_finding(row) for row in items],
            }
        )
    limits = []
    if eligible:
        total = f"{len(rows)} {documents.plural(len(rows), OF_ROWS)}"
        basis = f"Вывод опирается на {len(eligible)} из {total}"
        unreviewed = any(row.measure.review_status == "unreviewed" for row in eligible)
        limits.append(f"{basis}; {NOT_REVIEWED}." if unreviewed else f"{basis}.")
    if checked := [row.number for row in eligible if _checked_manually(row)]:
        verb = CHECKED_MANUALLY_VERB[len(checked) > 1]
        limits.append(
            f"{_span(checked)} — {verb} с документом пользователем, "
            "автоматическая сверка не прошла."
        )
    if len(eligible) > 1:
        limits.append(DIFFERENT_DEFINITIONS)
    excluded_reasons = []
    for exclusion, reason in EXCLUSIONS.items():
        excluded = [row.number for row in rows if _exclusion(row) == exclusion]
        if excluded:
            excluded_reasons.append(f"{_span(excluded)} — {reason}.")
    if eligible:
        absent = None
    elif rows:
        count = f"{len(rows)} {documents.plural(len(rows), OF_ROWS)}"
        absent = (
            f"Нет допустимых строк: ни одна из {count} не может войти в вывод. "
            "Причины — ниже и в охвате."
        )
    else:
        absent = NO_MEASURES
    return {
        "findings": findings,
        "limits": limits,
        "absent_reason": absent,
        "excluded_count": len(rows) - len(eligible),
        "excluded_reasons": excluded_reasons,
        "works": _absent_works(inputs, rows, eligible),
    }


async def _question_and_search(db: AsyncSession, study_id: int) -> tuple[str, dict]:
    """Вопрос и параметры поиска, по которым найдены работы."""
    run = await search.latest_run(db, study_id)
    assert run is not None
    version = await db.get(CriteriaVersionRecord, run.criteria_version_id)
    assert version is not None
    question = await db.get(questions.QuestionVersionRecord, version.question_version_id)
    assert question is not None
    fields = version.fields
    return question.text, {
        "period": fields["period"],
        "publication_kinds": [PUBLICATION_KINDS.get(k, k) for k in fields["publication_kinds"]],
        "conditions": [CONDITIONS.get(c, c) for c in fields["conditions"]],
        "resources": fields["resources"],
        "queries": [
            {"resource": source.resource, "query": source.query}
            for source in await search.source_runs(db, run.id)
        ],
    }


# ---------- Ревизия ----------


@dataclass(frozen=True)
class Report:
    """Ревизия справки в форме API «Результат»."""

    revision: dict
    question: str
    search: dict
    summary: dict
    coverage: dict
    measures: list[dict]
    allowed_actions: list[str]


async def _latest(db: AsyncSession, study_id: int) -> ReportRevisionRecord | None:
    return await db.scalar(
        select(ReportRevisionRecord)
        .where(ReportRevisionRecord.study_id == study_id)
        .order_by(ReportRevisionRecord.number.desc())
        .limit(1)
    )


async def _record(
    db: AsyncSession, study_id: int, number: int, inputs: dict
) -> ReportRevisionRecord:
    card_revision_ids = [w["card_revision_id"] for w in inputs["works"] if w["card_revision_id"]]
    rows = await _card_rows(db, card_revision_ids)
    question, parameters = await _question_and_search(db, study_id)
    report = ReportRevisionRecord(
        study_id=study_id,
        number=number,
        summary_revision=number,
        stale_reason=None,
        inputs=inputs,
        content={
            "question": question,
            "search": parameters,
            "coverage": _coverage(inputs, rows),
            "summary": _summary(inputs, rows),
        },
        created_at=datetime.now(UTC),
    )
    db.add(report)
    await db.flush()
    db.add_all(
        ReportCardRevisionRecord(report_revision_id=report.id, card_revision_id=revision_id)
        for revision_id in card_revision_ids
    )
    db.add_all(
        ReportMeasureRecord(
            report_revision_id=report.id, measure_id=row.measure.id, number=row.number
        )
        for row in rows
    )
    return report


async def current(db: AsyncSession, account_id: int) -> Report | None:
    """Текущая ревизия справки; новая записывается, если её входы изменились.

    None — исследования нет или ни одна выбранная работа ещё не обрабатывалась.
    """
    study = await questions.find_study(db, account_id)
    if study is None:
        return None
    inputs = await _inputs(db, account_id, study.id)
    if inputs is None:
        return None
    report = await _latest(db, study.id)
    if report is None or report.inputs != inputs:
        number = report.number + 1 if report else 1
        try:
            report = await _record(db, study.id, number, inputs)
            await db.commit()
        except IntegrityError:
            # Ту же ревизию записал параллельный запрос.
            await db.rollback()
            report = await _latest(db, study.id)
            assert report is not None
    return await _view(db, report)


async def _view(db: AsyncSession, report: ReportRevisionRecord) -> Report:
    works = report.inputs["works"]
    return Report(
        revision={
            "number": report.number,
            "summary_revision": report.summary_revision,
            "freshness": "stale" if report.summary_revision < report.number else "current",
            "stale_reason": report.stale_reason,
            "complete": all(work["outcome"] is not None for work in works),
        },
        question=report.content["question"],
        search=report.content["search"],
        summary=report.content["summary"],
        coverage=report.content["coverage"],
        measures=_table(await _pinned_rows(db, report)),
        # Обновление справки — #18, экспорт — #19.
        allowed_actions=[],
    )


# ---------- Основание строки ----------


class NotFound(Exception):
    """Показателя нет в карточках текущего исследования."""


def _printed(locator: dict | None) -> str:
    """Адрес основания словами."""
    if locator is None:
        return "адрес не установлен"
    match locator["kind"]:
        case "pdf":
            return f"страница {locator['page']}"
        case "jats":
            return f"узел {locator.get('id') or locator['path']}"
        case "jats_row" | "jats_cell":
            text = f"{locator.get('label') or 'таблица'} · строка {locator['row']}"
            if locator["kind"] == "jats_cell":
                headers = locator.get("headers") or []
                named = f" «{headers[0]}»" if headers else ""
                text += f" · столбец {locator['column']}{named}"
            return text
        case "csv":
            text = f"запись {locator['record']}"
            if "column" in locator:
                named = f" «{locator['header']}»" if locator.get("header") else ""
                text += f" · столбец {locator['column']}{named}"
            return text
    return "адрес не установлен"


def _locator(locator: dict | None) -> dict | None:
    if locator is None:
        return None
    headers = locator.get("headers")
    if locator["kind"] == "csv" and locator.get("header"):
        headers = [locator["header"]]
    return {
        "kind": locator["kind"],
        "page": locator.get("page"),
        "printed": _printed(locator),
        "context": None,
        "path": locator.get("path"),
        "node_id": locator.get("id"),
        "table": locator.get("label"),
        "row": locator.get("row"),
        "column": locator.get("column"),
        "headers": headers or [],
        "record": locator.get("record"),
    }


def _precision_note(item: dict, version: DocumentVersionRecord, found: bool) -> str | None:
    """Почему точный фрагмент не показан; фрагмент не подменяется пересказом."""
    if found:
        return None
    locator = item.get("locator")
    if version.parsed is None:
        return f"Точный фрагмент не показан: {evidence.NOT_PARSED.rstrip('.').lower()}."
    if locator is None:
        reason = item.get("diagnostic") or "адрес основания не установлен."
        return f"Точный фрагмент не показан: {reason[0].lower()}{reason[1:]}"
    if locator.get("page"):
        return f"Точный фрагмент не выделен: откройте документ на странице {locator['page']}."
    return f"Точный фрагмент не выделен: откройте документ, {_printed(locator)}."


async def _source(db: AsyncSession, publication: PublicationRecord) -> dict:
    provenance = await selection.provenance(db, publication)
    return {
        "resource": ", ".join(RESOURCES[resource] for resource in provenance) or "не указан",
        "identifier": publication.doi or publication.pmcid or publication.pmid or "не указан",
        "source_type": VERSION_TYPES.get(publication.version_type, publication.version_type),
        "year": publication.year,
    }


async def measure_evidence(db: AsyncSession, account_id: int, measure_id: int) -> list[dict]:
    """Основания значения и контекста строки: адрес, версия документа и реальный фрагмент."""
    study = await questions.find_study(db, account_id)
    measure = await db.get(MeasureRecord, measure_id)
    revision = await db.get(CardRevisionRecord, measure.revision_id) if measure else None
    publication = await db.get(PublicationRecord, revision.publication_id) if revision else None
    if study is None or measure is None or publication is None or publication.study_id != study.id:
        raise NotFound
    source = await _source(db, publication)
    found = []
    for role, key in (("value", "evidence"), ("context", "context_evidence")):
        for n, item in enumerate(measure.content[key], 1):
            version = await db.get(DocumentVersionRecord, item["document_version_id"])
            assert version is not None
            document = await db.get(DocumentRecord, version.document_id)
            assert document is not None
            fragment = evidence.fragment(item, version.parsed)
            found.append(
                {
                    "id": _evidence_id(measure.id, role, n),
                    "measure_id": str(measure.id),
                    "role": role,
                    "publication_label": documents.label(publication),
                    "document_id": str(document.id),
                    "document_version_id": str(version.id),
                    "document_name": document.name
                    if document.role == "main"
                    else version.file_name,
                    "document_version": version.number,
                    "quote": item["quote"],
                    "claimed_location": item["location"],
                    "locator": _locator(item.get("locator")),
                    "fragment": fragment,
                    "precision_note": _precision_note(item, version, fragment is not None),
                    "diagnostic": item.get("diagnostic"),
                    "source": source,
                    "verification_status": item["verification_status"],
                }
            )
    return found
