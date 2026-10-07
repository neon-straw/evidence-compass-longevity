import { HttpResponse, http } from "msw";
import type { Schemas } from "../api/types";
import corpus from "./data/measures.json";
import { workOutcomes } from "./extraction";
import { foundCount, searchQueries, selectedWorks } from "./search";
import { appliedScenario, studyDemo } from "./study";

/**
 * Демо-состояние шага «Результат» (состояния 23–24 из docs/ux/stage-1-shape.md §9).
 * Строки — кандидатные показатели data/corpus/expected-results.csv без изменения значений;
 * до сверки пользователем все они «Требует проверки». Исходные PDF не в репозитории: основание
 * показывает адрес и ограничение точности; у строк Clubb 2008 — явно помеченный демо-фрагмент.
 * Автоматическая сверка в демо прошла у всех строк, кроме `UNMATCHED`: по строке каждого
 * состояния сверки, одна из них уже сверена пользователем. Строка `NOT_OWN_RESULT` — значение
 * по другим работам.
 */
type Row = Schemas["MeasureRow"];
type Evidence = Schemas["Evidence"];
type CorpusEntry = {
  row: Row;
  evidence: {
    document_name: string;
    document_version: number;
    page: number | null;
    printed: string;
    context: string | null;
    doi: string;
  };
};

type Correction = { value_original: string; unit_original: string; comment: string };
type State = {
  // Ревизия таблицы растёт с каждой правкой; краткий вывод — по `summaryRevision`.
  tableRevision: number;
  summaryRevision: number;
  staleReason: string | null;
  corrections: Record<string, Correction>;
  reviews: Record<string, Row["review_status"]>;
};

const STORAGE_KEY = "ec-demo-result";
const UNMATCHED: Record<string, NonNullable<Row["verification_gap"]>> = {
  "shark-10": "quote_not_found",
  "shark-11": "value_not_matched",
  "shark-12": "value_not_matched",
};
const DEMO_REVIEWS: Record<string, Row["review_status"]> = { "shark-12": "checked" };
// Значение по другим работам: сверено, но в вывод не входит.
const NOT_OWN_RESULT = new Set(["shark-13"]);
const ENTRIES = corpus as unknown as CorpusEntry[];
const pad = (n: number) => String(n).padStart(2, "0");

const KIND_LABEL: Record<Row["measure_kind"], string> = {
  mean: "Среднее",
  median: "Медиана",
  observed_maximum: "Наблюдаемый максимум",
  individual_age: "Возраст особи",
  modelled_age: "Модельная оценка возраста",
  survival_interval: "Интервал выживания",
  maturity_age: "Возраст созревания",
  other: "Другой показатель",
};

function initialState(): State {
  if (studyDemo.scenario() !== "result-stale") {
    return {
      tableRevision: 1,
      summaryRevision: 1,
      staleReason: null,
      corrections: {},
      reviews: { ...DEMO_REVIEWS },
    };
  }
  const first = ENTRIES[0].row;
  return {
    tableRevision: 2,
    summaryRevision: 1,
    staleReason: `Строка ${pad(first.number)} исправлена после формирования краткого вывода.`,
    corrections: {
      [first.id]: {
        value_original: first.value_original,
        unit_original: "years (Демо · уточнено)",
        comment: "Демо: уточнены единицы по таблице S2.",
      },
    },
    reviews: { ...DEMO_REVIEWS, [first.id]: "corrected" },
  };
}

function load(): State {
  const stored = sessionStorage.getItem(STORAGE_KEY);
  if (appliedScenario || !stored) return initialState();
  return JSON.parse(stored) as State;
}

let state = load();
function persist() {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}
persist();

/** Строки только тех работ, которые дали показатели в последнем извлечении. */
function entries(): CorpusEntry[] {
  const extracted = new Set(
    workOutcomes()
      .filter((w) => w.outcome === "measures_extracted")
      .map((w) => w.publication_id),
  );
  return ENTRIES.filter((e) => extracted.has(e.row.publication_id));
}

function row(entry: CorpusEntry): Row {
  const correction = state.corrections[entry.row.id];
  return {
    ...entry.row,
    ...(correction && {
      value_original: correction.value_original,
      unit_original: correction.unit_original,
    }),
    review_status: state.reviews[entry.row.id] ?? entry.row.review_status,
    verification_status: UNMATCHED[entry.row.id] ? "needs_review" : "verified",
    verification_gap: UNMATCHED[entry.row.id] ?? null,
    origin_kind: NOT_OWN_RESULT.has(entry.row.id) ? "secondary_report" : entry.row.origin_kind,
  };
}

/** Как на сервере: несверенная строка входит в вывод, только если её сверил пользователь;
 * значение по другим работам не входит. Порядок — приоритет причины. */
const checkedManually = (r: Row) => r.verification_gap !== null && r.review_status === "checked";
const EXCLUSIONS: [string, (r: Row) => boolean][] = [
  [
    "не собственный результат работы: значение приведено по другим работам или как известный факт, первоисточник не проверен",
    (r) => r.origin_kind === "secondary_report",
  ],
  [
    "число с единицами не найдено в тексте документа по цитате, нужна сверка с документом; после сверки отметьте строку проверенной",
    (r) => r.verification_gap !== null && !checkedManually(r),
  ],
];
const exclusion = (r: Row) => EXCLUSIONS.find(([, applies]) => applies(r))?.[0];

function span(numbers: number[]): string {
  const [first, last] = [Math.min(...numbers), Math.max(...numbers)];
  if (first === last) return `строка ${pad(first)}`;
  if (last - first + 1 === numbers.length) return `строки ${pad(first)}–${pad(last)}`;
  return `строки ${numbers.map(pad).join(", ")}`;
}

const upper = (text: string) => text[0].toUpperCase() + text.slice(1);

function coverageWorks(): Schemas["CoverageWork"][] {
  return workOutcomes().map((w) => ({
    publication_id: w.publication_id,
    label: w.label,
    outcome: w.outcome,
    measure_count: w.outcome === "measures_extracted" ? (w.measure_count ?? 0) : 0,
    reason:
      w.outcome === "measures_extracted"
        ? null
        : (w.detail ?? (w.execution === "skipped" ? "не обработана" : "без показателей")),
  }));
}

/** Демо отложенного из карточки: реальный вид и описание, не общая фраза (issue #42). */
const DEMO_SET_ASIDE: Record<string, Schemas["SetAsideItem"][]> = {
  podlutsky2005: [
    { kind: "other_species", description: "Демо: показатель приведён для другого вида колонии" },
  ],
};

/** Демо: по строке на работу без показателей, с её собственными причинами, а не одна
 * склеенная фраза (issue #42). Причина итога — только у «Требует решения» (`unresolved`):
 * остальным итогам вывод не дублирует пояснение из подробного охвата. */
function absentWorks(): Schemas["SummaryWork"][] {
  return workOutcomes()
    .filter((w) => w.outcome !== "measures_extracted")
    .map((w) => ({
      publication_id: w.publication_id,
      label: w.label.split(" · ")[0],
      outcome: w.outcome,
      unresolved: w.outcome === "unresolved" && w.detail ? [w.detail.replace(/\.$/, "")] : [],
      set_aside: DEMO_SET_ASIDE[w.publication_id] ?? [],
      gaps: w.gaps.map((g) => g.replace(/\.$/, "")),
    }))
    .sort((a, b) => Number(a.outcome !== "unresolved") - Number(b.outcome !== "unresolved"));
}

function summary(rows: Row[]): Schemas["Summary"] {
  if (rows.length === 0) {
    return {
      findings: [],
      limits: [],
      excluded_count: 0,
      excluded_reasons: [],
      works: absentWorks(),
      absent_reason:
        "Нет допустимых строк: ни одна обработанная работа не дала опубликованных показателей. Причины — в охвате.",
    };
  }
  const included = rows.filter((r) => !exclusion(r));
  const excluded = rows.filter((r) => exclusion(r));
  const manual = included.filter(checkedManually).map((r) => r.number);
  const bySpecies = new Map<string, Row[]>();
  for (const r of included) {
    const key = r.species_label.toLowerCase().includes(r.species_name_in_source.toLowerCase())
      ? r.species_label
      : `${r.species_label} (${r.species_name_in_source})`;
    bySpecies.set(key, [...(bySpecies.get(key) ?? []), r]);
  }
  return {
    findings: [...bySpecies].map(([label, items]) => ({
      text: `Демо: ${items[0].species_name_in_source} — ${[...new Set(items.map((r) => KIND_LABEL[r.measure_kind]))].join(", ")} (${label}, ${span(items.map((r) => r.number))}).`,
      measure_refs: items.map((r) => r.id),
      species: label,
      items: items.map((r) => ({
        measure_id: r.id,
        number: r.number,
        title: `${KIND_LABEL[r.measure_kind]} — ${r.qualifier ? `${r.qualifier} ` : ""}${r.value_original}${r.uncertainty?.plus_minus ? ` ± ${r.uncertainty.plus_minus}` : ""} ${r.unit_original}${r.uncertainty ? ` (${r.uncertainty.label})` : ""}`,
        context: `${CONDITIONS[r.conditions.setting] ?? "условия не указаны"} · ${r.subject.label}`,
        publication: r.publication_label.split(" · ")[0],
        checked_manually: checkedManually(r),
      })),
    })),
    excluded_count: excluded.length,
    excluded_reasons: EXCLUSIONS.flatMap(([reason]) => {
      const numbers = excluded.filter((r) => exclusion(r) === reason).map((r) => r.number);
      return numbers.length ? [`${upper(span(numbers))} — ${reason}.`] : [];
    }),
    limits: [
      ...(manual.length
        ? [
            `${upper(span(manual))} — ${manual.length > 1 ? "сверены" : "сверена"} с документом пользователем, автоматическая сверка не прошла.`,
          ]
        : []),
      "Показатели имеют разные определения и условия и не сводятся к одному значению вида.",
      "Строки не проверены пользователем: вывод предварительный и не является научным подтверждением.",
    ],
    works: absentWorks(),
    absent_reason: null,
  };
}

const KINDS: Record<string, string> = {
  journal_article: "статьи",
  preprint: "препринты",
  other: "другие",
};
const CONDITIONS: Record<string, string> = { wild: "природа", captive: "неволя" };

function result(): Schemas["Result"] {
  const rows = entries().map(row);
  const works = coverageWorks();
  const selected = selectedWorks().length;
  const unavailable = works.filter((w) => w.outcome === "unavailable").length;
  const notProcessed = works.filter((w) => w.outcome === null).length;
  const stale = state.summaryRevision < state.tableRevision;
  const criteria = studyDemo.criteria();
  return {
    revision: {
      number: state.tableRevision,
      summary_revision: state.summaryRevision,
      freshness: stale ? "stale" : "current",
      stale_reason: stale ? state.staleReason : null,
      complete: works.every((w) => w.outcome === "measures_extracted"),
    },
    question: studyDemo.question(),
    search: {
      period: criteria.period,
      publication_kinds: criteria.publication_kinds.map((k) => KINDS[k] ?? k),
      conditions: criteria.conditions.map((c) => CONDITIONS[c] ?? c),
      resources: criteria.resources,
      queries: searchQueries(),
    },
    summary: summary(rows),
    coverage: {
      found: foundCount(),
      selected,
      full_texts: selected - unavailable,
      unavailable,
      not_processed: notProcessed,
      share: selected
        ? works.filter((w) => w.outcome === "measures_extracted").length / selected
        : 0,
      works,
    },
    measures: rows,
    allowed_actions: stale ? ["refresh", "export"] : ["export"],
  };
}

const entryById = (id: string) => entries().find((e) => e.row.id === id);

/** Адрес PDF в демо: печатное место из корпуса; структурных полей JATS/CSV нет. */
function locator(page: number | null, printed: string): Schemas["Locator"] {
  return {
    kind: "pdf",
    page,
    printed: page ? `страница ${page} · ${printed}` : printed,
    context: null,
    path: null,
    node_id: null,
    table: null,
    row: null,
    column: null,
    headers: [],
    record: null,
  };
}

/** Демо-фрагмент строк Clubb 2008: помечен в интерфейсе как демо, не цитата источника. */
function demoFragment(r: Row): Schemas["FragmentLine"][] | null {
  if (r.publication_id !== "clubb2008") return null;
  const bounds = r.uncertainty ? ` (${r.uncertainty.label})` : "";
  return [
    { number: 10, text: "…", highlighted: false },
    { number: 11, text: `${r.subject.label} · ${r.conditions.text}`, highlighted: false },
    {
      number: 12,
      text: `${r.definition}: ${r.value_original} ${r.unit_original}${bounds}`,
      highlighted: true,
    },
    { number: 13, text: "…", highlighted: false },
  ];
}

function evidence(entry: CorpusEntry): Evidence[] {
  const e = entry.evidence;
  const r = row(entry);
  const base = {
    measure_id: r.id,
    publication_label: r.publication_label,
    document_id: `${r.publication_id}-main`,
    document_version_id: `${r.publication_id}-main-v${e.document_version}`,
    document_name: e.document_name,
    document_version: e.document_version,
    source: {
      resource: "Europe PMC",
      identifier: e.doi,
      source_type: "статья",
      year: Number(r.publication_label.slice(-4)) || null,
    },
    verification_status: r.verification_status,
    diagnostic: null,
    // В демо цитат модели нет: панель объясняет ограничение через `precision_note`.
    quote: "",
    claimed_location: e.printed,
  };
  const fragment = demoFragment(r);
  const value: Evidence = {
    ...base,
    id: r.evidence_ids[0],
    role: "value",
    locator: locator(e.page, e.printed),
    fragment,
    precision_note: fragment
      ? null
      : `Точный фрагмент в демо недоступен: исходные документы не входят в репозиторий. Откройте документ на странице ${e.page ?? "—"}: ${e.printed}.`,
  };
  const context: Evidence[] = r.context_evidence_ids.map((id) => ({
    ...base,
    id,
    role: "context",
    locator: locator(null, e.context ?? ""),
    fragment: null,
    precision_note: "Основание методов и условий: адрес указан, фрагмент в демо недоступен.",
  }));
  return [value, ...context];
}

/** CSV ревизии таблицы: null — пустая ячейка, текст защищён от формул таблиц. */
function csv(rows: Row[]): string {
  const columns: [string, (r: Row, e: CorpusEntry["evidence"]) => unknown][] = [
    ["table_revision", () => state.tableRevision],
    ["summary_revision", () => state.summaryRevision],
    ["row", (r) => r.number],
    ["measure_id", (r) => r.id],
    ["species_name_in_source", (r) => r.species_name_in_source],
    ["taxonomy_note", (r) => r.taxonomy_note],
    ["measure_kind", (r) => r.measure_kind],
    ["definition", (r) => r.definition],
    ["value_original", (r) => r.value_original],
    ["unit_original", (r) => r.unit_original],
    ["qualifier", (r) => r.qualifier],
    ["uncertainty_label", (r) => r.uncertainty?.label],
    ["uncertainty_kind", (r) => r.uncertainty?.kind],
    ["uncertainty_level_percent", (r) => r.uncertainty?.level_percent],
    ["uncertainty_plus_minus", (r) => r.uncertainty?.plus_minus],
    ["uncertainty_lower", (r) => r.uncertainty?.lower],
    ["uncertainty_upper", (r) => r.uncertainty?.upper],
    ["uncertainty_upper_status", (r) => r.uncertainty?.upper_status],
    ["subject", (r) => r.subject.label],
    ["sex", (r) => r.subject.sex],
    ["sample_size", (r) => r.subject.sample_size],
    ["conditions_setting", (r) => r.conditions.setting],
    ["conditions", (r) => r.conditions.text],
    ["method", (r) => r.method],
    ["author_notes", (r) => r.author_notes],
    ["origin_kind", (r) => r.origin_kind],
    ["review_status", (r) => r.review_status],
    ["publication", (r) => r.publication_label],
    ["doi", (_, e) => e.doi],
    ["document", (_, e) => e.document_name],
    ["document_version", (_, e) => e.document_version],
    ["pdf_page", (_, e) => e.page],
    ["locator", (_, e) => e.printed],
    // Все основания строки одним однозначным JSON-массивом: дополнительные не теряются.
    ["evidence_ids", (r) => JSON.stringify([...r.evidence_ids, ...r.context_evidence_ids])],
  ];
  const cell = (value: unknown) => {
    if (value === null || value === undefined) return "";
    let text = String(value);
    if (/^[=+\-@\t\r]/.test(text) && Number.isNaN(Number(text))) text = `'${text}`;
    return /[",\n\r\t;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = rows.map((r) =>
    columns.map(([, value]) => cell(value(r, entryById(r.id)!.evidence))).join(","),
  );
  return [columns.map(([name]) => name).join(","), ...lines].join("\n") + "\n";
}

const notFound = () => HttpResponse.json({ detail: "Строка не найдена" }, { status: 404 });

export const resultHandlers = [
  http.get("/api/result", () => HttpResponse.json<Schemas["Result"]>(result())),
  http.get<{ id: string }>("/api/measures/:id/evidence", ({ params }) => {
    const entry = entryById(params.id);
    return entry ? HttpResponse.json<Evidence[]>(evidence(entry)) : notFound();
  }),
  http.post<{ id: string }, Schemas["MeasureReview"]>(
    "/api/measures/:id/review",
    async ({ params, request }) => {
      const entry = entryById(params.id);
      if (!entry) return notFound();
      state.reviews[params.id] = (await request.json()).review_status;
      persist();
      return HttpResponse.json<Row>(row(entry));
    },
  ),
  http.post<{ id: string }, Schemas["MeasureCorrection"]>(
    "/api/measures/:id/correction",
    async ({ params, request }) => {
      const entry = entryById(params.id);
      if (!entry) return notFound();
      const body = await request.json();
      if (body.expected_revision !== state.tableRevision) {
        return HttpResponse.json<Schemas["Problem"]>(
          { target: params.id, message: "Таблица изменилась: обновите экран и повторите правку." },
          { status: 409 },
        );
      }
      const { value_original, unit_original, comment } = body;
      state.corrections[params.id] = { value_original, unit_original, comment };
      state.reviews[params.id] = "corrected";
      // Правка — новая ревизия таблицы; краткий вывод остаётся на прежней до обновления.
      state.tableRevision += 1;
      state.staleReason = `Строка ${pad(entry.row.number)} исправлена после формирования краткого вывода.`;
      persist();
      return HttpResponse.json<Row>(row(entry));
    },
  ),
  http.post("/api/result/refresh", () => {
    state.summaryRevision = state.tableRevision;
    state.staleReason = null;
    persist();
    return HttpResponse.json<Schemas["Result"]>(result());
  }),
  http.get("/api/result/export", ({ request }) => {
    const revision = Number(new URL(request.url).searchParams.get("revision"));
    if (revision !== state.tableRevision) {
      return HttpResponse.json<Schemas["Problem"]>(
        { target: "export", message: `Ревизия ${revision} больше не текущая: обновите экран.` },
        { status: 409 },
      );
    }
    return new HttpResponse(csv(entries().map(row)), {
      headers: { "Content-Type": "text/csv; charset=utf-8" },
    });
  }),
];
