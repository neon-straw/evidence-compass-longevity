import { HttpResponse, delay, http } from "msw";
import type { Schemas } from "../api/types";
import { appliedScenario, studyDemo } from "./study";

/**
 * Демо-состояние шагов «Поиск» и «Отбор» (состояния 13–18 из docs/ux/stage-1-shape.md §9).
 * Четыре работы — реальные записи корпуса data/corpus; остальные помечены «Демо ·».
 */
type Row = Schemas["PublicationRow"];
type Card = Schemas["PublicationCard"];
type Source = Schemas["SourceRun"];
type Resource = Source["resource"];

type Outcome = {
  status: "hits" | "empty" | "error";
  hits: number;
  returned?: number;
  error?: string;
};

type State = {
  searchStatus: "not_started" | "running" | "completed" | "stopped";
  startedAt: number;
  stoppedAt: number;
  // Карточки публикаций; строка таблицы — их подмножество.
  cards: Card[];
  seeded: boolean;
};

const STORAGE_KEY = "ec-demo-search";
const LIMIT = 8;
const FINISH_MS: Record<Resource, number> = { pubmed: 1500, europe_pmc: 3000 };

function outcomes(): Record<Resource, Outcome> {
  switch (studyDemo.scenario()) {
    case "search-limited":
      return {
        pubmed: { status: "hits", hits: 18 },
        europe_pmc: { status: "hits", hits: 630, returned: 200 },
      };
    case "search-empty":
      return { pubmed: { status: "empty", hits: 0 }, europe_pmc: { status: "empty", hits: 0 } };
    case "search-partial":
      return {
        pubmed: { status: "hits", hits: 18 },
        europe_pmc: {
          status: "error",
          hits: 0,
          error: "Europe PMC ответил 503: сервис недоступен.",
        },
      };
    case "search-failed":
      return {
        pubmed: { status: "error", hits: 0, error: "PubMed не ответил за 30 секунд." },
        europe_pmc: {
          status: "error",
          hits: 0,
          error: "Europe PMC ответил 503: сервис недоступен.",
        },
      };
    default:
      return { pubmed: { status: "hits", hits: 18 }, europe_pmc: { status: "hits", hits: 12 } };
  }
}

// ---------- Публикации ----------

type Seed = Partial<Card> & Pick<Card, "id" | "title" | "year">;

const REAL: Seed[] = [
  {
    id: "ruby2018",
    // Europe PMC: isOpenAccess=Y, как в data/corpus/search-responses/ruby2018-epmc.json.
    open_access: "open",
    title: "Naked Mole-Rat mortality rates defy gompertzian laws by not increasing with age.",
    year: 2018,
    authors: "Ruby JG, Smith M, Buffenstein R.",
    doi: "10.7554/eLife.31157",
    abstract:
      "The longest-lived rodent, the naked mole-rat (Heterocephalus glaber), has a reported maximum lifespan of >30 years and exhibits delayed and/or attenuated age-associated physiological declines.",
    sources: [
      {
        resource: "pubmed",
        identifier: "PMID 29364116",
        url: "https://pubmed.ncbi.nlm.nih.gov/29364116/",
      },
      {
        resource: "europe_pmc",
        identifier: "PMC5783610",
        url: "https://europepmc.org/article/PMC/PMC5783610",
      },
    ],
  },
  {
    id: "nielsen2016",
    title:
      "Eye lens radiocarbon reveals centuries of longevity in the Greenland shark (Somniosus microcephalus).",
    year: 2016,
    authors: "Nielsen J, Hedeholm RB, Heinemeier J, Bushnell PG, Christiansen JS, Olsen J, et al.",
    doi: "10.1126/science.aaf1703",
    abstract:
      "The Greenland shark (Somniosus microcephalus), an iconic species of the Arctic Seas, grows slowly and reaches >500 centimeters (cm) in total length.",
    sources: [
      {
        resource: "pubmed",
        identifier: "PMID 27516602",
        url: "https://pubmed.ncbi.nlm.nih.gov/27516602/",
      },
      {
        resource: "europe_pmc",
        identifier: "MED/27516602",
        url: "https://europepmc.org/article/MED/27516602",
      },
    ],
  },
  {
    id: "podlutsky2005",
    title: "A new field record for bat longevity.",
    year: 2005,
    authors: "Podlutsky AJ, Khritankov AM, Ovodov ND, Austad SN.",
    doi: "10.1093/gerona/60.11.1366",
    abstract:
      "A new longevity record has been established for a free-living bat-41 years for a male Brandt's bat, Myotis brandtii.",
    sources: [
      {
        resource: "pubmed",
        identifier: "PMID 16339320",
        url: "https://pubmed.ncbi.nlm.nih.gov/16339320/",
      },
    ],
  },
  {
    id: "clubb2008",
    title: "Compromised survivorship in zoo elephants.",
    year: 2008,
    authors: "Clubb R, Rowcliffe M, Lee P, Mar KU, Moss C, Mason GJ.",
    doi: "10.1126/science.1164298",
    abstract:
      "We analyzed data from over 4500 elephants to show that animals in European zoos have about half the median life span of conspecifics in protected populations.",
    sources: [
      {
        resource: "europe_pmc",
        identifier: "MED/19074339",
        url: "https://europepmc.org/article/MED/19074339",
      },
    ],
  },
];

const SPECIES = [
  "Heterocephalus glaber",
  "Somniosus microcephalus",
  "Myotis brandtii",
  "Loxodonta africana",
];
const TOPICS = [
  "molecular mechanisms of extreme longevity",
  "lifespan and resistance to age-related disease",
  "reproductive senescence and longevity",
  "genomic features of lifespan extension",
  "age estimation study",
];

function demoSeeds(): Seed[] {
  const seeds: Seed[] = [];
  let n = 0;
  for (const topic of TOPICS) {
    for (const species of SPECIES) {
      if (seeds.length >= 18) break;
      n += 1;
      const decision: Row["decision"] =
        n % 3 === 0 ? "excluded" : n % 4 === 0 ? "needs_review" : "included";
      seeds.push({
        id: `demo-${n}`,
        title: `Демо · ${species}: ${topic}`,
        year: 2010 + (n % 13),
        version_type: n % 5 === 0 ? "preprint" : "journal",
        decision,
        open_access: n % 3 === 0 ? "closed" : n % 7 === 0 ? "unknown" : "open",
        authors: "Демо · A. Researcher, B. Author",
        doi: `10.1234/demo.${2010 + (n % 13)}.${String(n).padStart(3, "0")}`,
        abstract: `Демо: публикация описывает ${topic} у ${species}. Это демонстрационный текст.`,
        sources: [
          { resource: n % 2 ? "pubmed" : "europe_pmc", identifier: `Демо · ${n}`, url: null },
        ],
      });
    }
  }
  // Препринт и журнальная версия одной работы: связь не подтверждена (состояние 16).
  const pairTitle = "Демо · Loxodonta africana: comparative longevity and cancer resistance";
  seeds.push(
    {
      id: "demo-pair-preprint",
      title: `${pairTitle} (preprint)`,
      year: 2020,
      version_type: "preprint",
      decision: "needs_review",
      open_access: "open",
      authors: "Демо · A. Researcher, B. Author",
      doi: "10.1101/demo.2020.004",
      abstract:
        "Демо: препринт сравнительного анализа продолжительности жизни. Демонстрационный текст.",
      sources: [{ resource: "europe_pmc", identifier: "Демо · PPR123456", url: null }],
    },
    {
      id: "demo-pair-journal",
      title: pairTitle,
      year: 2021,
      version_type: "journal",
      decision: "included",
      open_access: "open",
      authors: "Демо · A. Researcher, B. Author",
      doi: "10.1234/demo.2021.004",
      abstract: "Демо: журнальная версия того же анализа. Демонстрационный текст.",
      sources: [
        { resource: "pubmed", identifier: "Демо · PMID 1234567", url: null },
        { resource: "europe_pmc", identifier: "Демо · PMC1234567", url: null },
      ],
    },
  );
  return seeds;
}

const REASONS: Record<Row["decision"], string> = {
  included: "Объект и условия соответствуют подтверждённым критериям поиска.",
  excluded: "Нет опубликованных показателей возраста или продолжительности жизни.",
  needs_review: "Соответствие условиям наблюдения неясно по аннотации.",
};

function card(seed: Seed): Card {
  const decision = seed.decision ?? "included";
  const sources = seed.sources ?? [];
  return {
    version_type: "journal",
    open_access: "closed",
    authors: "",
    doi: null,
    abstract: null,
    ...seed,
    decision,
    provenance: [...new Set(sources.map((s) => s.resource))],
    sources,
    versions: 1,
    version_link: "none",
    selected: false,
    select_blocked_reason: null,
    decision_record: {
      decision,
      reason: REASONS[decision],
      author: "system",
      criteria_version: studyDemo.confirmedVersion(),
    },
    related_versions: [],
  };
}

function seedCards(scenario: string, pubmedOnly: boolean): Card[] {
  let cards = [...REAL, ...demoSeeds()].map(card);
  if (pubmedOnly) cards = cards.filter((c) => c.provenance.includes("pubmed")).slice(0, 18);
  const pair = cards.filter((c) => c.id.startsWith("demo-pair"));
  for (const c of pair) {
    c.versions = 2;
    c.version_link = "needs_review";
    c.related_versions = pair
      .filter((other) => other.id !== c.id)
      .map((other) => ({
        id: other.id,
        title: other.title,
        version_type: other.version_type,
        link: "needs_review",
      }));
  }
  if (scenario === "selection-none-included") {
    for (const c of cards)
      setDecision(c, c.decision === "included" ? "needs_review" : c.decision, "system");
  }
  const included = cards.filter((c) => c.decision === "included");
  const preselected =
    scenario === "selection-limit"
      ? LIMIT
      : scenario === "selection-default" || studyDemo.startsAt("preparation")
        ? 4
        : 0;
  included.slice(0, preselected).forEach((c) => (c.selected = true));
  return cards;
}

// ---------- Состояние ----------

function initialState(): State {
  const scenario = studyDemo.scenario();
  const selectionStage = studyDemo.startsAt("selection");
  return {
    searchStatus: selectionStage ? "completed" : "not_started",
    startedAt: selectionStage ? Date.now() - 10_000 : 0,
    stoppedAt: 0,
    cards: selectionStage ? seedCards(scenario, false) : [],
    seeded: selectionStage,
  };
}

function load(): State {
  const stored = sessionStorage.getItem(STORAGE_KEY);
  if (appliedScenario || !stored) return initialState();
  return JSON.parse(stored) as State;
}

let state = load();
const persist = () => sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
persist();

function sourceStatus(resource: Resource): Source["status"] {
  if (state.searchStatus === "not_started") return "pending";
  const done = Date.now() - state.startedAt >= FINISH_MS[resource];
  const stoppedBefore =
    state.searchStatus === "stopped" && state.stoppedAt - state.startedAt < FINISH_MS[resource];
  if (stoppedBefore) return "stopped";
  return done ? outcomes()[resource].status : "running";
}

function query(resource: Resource): string {
  const fields = studyDemo.criteria();
  const groups = [
    fields.objects.flatMap((object) => [object.scientific_name, ...(object.aliases ?? [])]),
    ...fields.search_criteria.map((criterion) => criterion.terms),
  ];
  return groups
    .filter((terms) => terms.length > 0)
    .map(
      (terms) =>
        `(${terms.map((t) => (resource === "pubmed" ? `"${t}"[tiab]` : `TITLE_ABS:"${t}"`)).join(" OR ")})`,
    )
    .join(" AND ");
}

function searchRun(): Schemas["SearchRun"] {
  const resources = studyDemo.criteria().resources;
  const sources: Source[] = resources.map((resource) => {
    const status = sourceStatus(resource);
    const outcome = outcomes()[resource];
    return {
      resource,
      query: query(resource),
      status,
      hit_count: status === "hits" || status === "empty" ? outcome.hits : null,
      returned_count:
        status === "hits" || status === "empty" ? (outcome.returned ?? outcome.hits) : null,
      error: status === "error" ? (outcome.error ?? null) : null,
    };
  });
  const finished = sources.every((s) => !["pending", "running"].includes(s.status));
  if (state.searchStatus === "running" && finished) {
    state.searchStatus = "completed";
    persist();
  }
  const allFailed = sources.every((s) => s.status === "error");
  if (state.searchStatus === "completed" && !allFailed && !state.seeded) {
    const pubmedOnly = sources.some((s) => s.status === "error");
    const noHits = sources.every((s) => s.status === "empty");
    state.cards = noHits ? [] : seedCards(studyDemo.scenario(), pubmedOnly);
    state.seeded = true;
    persist();
  }
  const blocked =
    state.searchStatus === "not_started"
      ? "Сначала запустите поиск."
      : state.searchStatus === "running"
        ? "Дождитесь завершения поиска."
        : state.searchStatus === "stopped"
          ? "Поиск остановлен: повторите поиск."
          : allFailed
            ? "Оба ресурса завершились ошибкой: повторите поиск или измените критерии."
            : null;
  if (!blocked) studyDemo.reach("selection");
  return {
    status: state.searchStatus,
    criteria_version: studyDemo.confirmedVersion(),
    progress:
      sources.filter((s) => !["pending", "running"].includes(s.status)).length / sources.length,
    sources,
    allowed_actions:
      state.searchStatus === "not_started"
        ? ["start"]
        : state.searchStatus === "running"
          ? ["stop"]
          : ["retry"],
    selection_blocked_reason: blocked,
  };
}

function setDecision(
  c: Card,
  decision: Row["decision"],
  author: "system" | "user",
  reason?: string,
) {
  c.decision = decision;
  c.decision_record = {
    decision,
    reason: reason || REASONS[decision],
    author,
    criteria_version: studyDemo.confirmedVersion(),
  };
  if (decision !== "included") c.selected = false;
}

const selectedCount = () => state.cards.filter((c) => c.selected).length;

function selectBlocked(c: Card): string | null {
  if (c.selected) return null;
  if (c.decision === "excluded")
    return "Исключённую работу выбрать нельзя: сначала измените решение.";
  if (selectedCount() >= LIMIT)
    return `Выбрано ${LIMIT} из ${LIMIT}: снимите выбор с другой работы.`;
  return null;
}

function row(c: Card): Row {
  const { abstract, sources, decision_record, related_versions, ...base } = c;
  void [abstract, sources, decision_record, related_versions];
  return { ...base, select_blocked_reason: selectBlocked(c) };
}

function selection(): Schemas["Selection"] {
  const count = selectedCount();
  const nothingToSelect = state.cards.every((c) => c.decision === "excluded");
  return {
    publications: state.cards.map(row),
    selected_count: count,
    limit: LIMIT,
    proceed_blocked_reason:
      count > 0
        ? null
        : nothingToSelect
          ? "Нет работ для выбора: измените решение или добавьте работу вручную."
          : "Выберите от одной до восьми работ.",
  };
}

const findCard = (id: string) => state.cards.find((c) => c.id === id);

// Каталог для ручного добавления: две записи, которых нет в выдаче, и одна уже найденная.
const CATALOG: Schemas["LookupCandidate"][] = [
  {
    title: "Демо · Balaena mysticetus: aspartic acid racemization age estimates",
    year: 1999,
    doi: "10.1234/demo.1999.101",
    pmid: "Демо · 1000001",
    already_listed: false,
  },
  {
    title: "Демо · Myotis brandtii: long-term banding record",
    year: 2016,
    doi: "10.1234/demo.2016.102",
    pmid: "Демо · 1000002",
    already_listed: false,
  },
];

export const searchHandlers = [
  http.get("/api/search", () => HttpResponse.json<Schemas["SearchRun"]>(searchRun())),
  http.post("/api/search/start", async () => {
    await delay(150);
    state = {
      ...state,
      searchStatus: "running",
      startedAt: Date.now(),
      stoppedAt: 0,
      seeded: false,
      cards: [],
    };
    persist();
    return HttpResponse.json<Schemas["SearchRun"]>(searchRun(), { status: 202 });
  }),
  http.post("/api/search/stop", () => {
    if (state.searchStatus === "running") {
      state.searchStatus = "stopped";
      state.stoppedAt = Date.now();
      persist();
    }
    return HttpResponse.json<Schemas["SearchRun"]>(searchRun());
  }),
  http.get("/api/selection", () => HttpResponse.json<Schemas["Selection"]>(selection())),
  http.get<{ id: string }>("/api/publications/:id", ({ params }) => {
    const c = findCard(params.id);
    return c
      ? HttpResponse.json<Card>({ ...c, select_blocked_reason: selectBlocked(c) })
      : HttpResponse.json({ detail: "Публикация не найдена" }, { status: 404 });
  }),
  http.put<{ id: string }, Schemas["DecisionChange"]>(
    "/api/publications/:id/decision",
    async ({ params, request }) => {
      const c = findCard(params.id);
      if (!c) return HttpResponse.json({ detail: "Публикация не найдена" }, { status: 404 });
      const { decision, reason } = await request.json();
      setDecision(c, decision, "user", reason);
      persist();
      return HttpResponse.json<Card>({ ...c, select_blocked_reason: selectBlocked(c) });
    },
  ),
  http.put<{ id: string }, Schemas["SelectionChange"]>(
    "/api/publications/:id/selected",
    async ({ params, request }) => {
      const c = findCard(params.id);
      if (!c) return HttpResponse.json({ detail: "Публикация не найдена" }, { status: 404 });
      const { selected } = await request.json();
      const blocked = selected ? selectBlocked(c) : null;
      if (blocked) {
        return HttpResponse.json<Schemas["Problem"]>(
          { target: c.id, message: blocked },
          { status: 409 },
        );
      }
      // Выбор записи, ждущей решения, включает её от имени пользователя.
      if (selected && c.decision === "needs_review")
        setDecision(c, "included", "user", "Отмечена соответствующей пользователем.");
      c.selected = selected;
      persist();
      return HttpResponse.json<Schemas["Selection"]>(selection());
    },
  ),
  http.post<{ id: string }, Schemas["VersionLinkChange"]>(
    "/api/publications/:id/version-links",
    async ({ params, request }) => {
      const c = findCard(params.id);
      const { related_id, same_work } = await request.json();
      const other = findCard(related_id);
      if (!c || !other)
        return HttpResponse.json({ detail: "Публикация не найдена" }, { status: 404 });
      for (const [a, b] of [
        [c, other],
        [other, c],
      ]) {
        a.version_link = same_work ? "confirmed" : "none";
        a.versions = same_work ? 2 : 1;
        a.related_versions = same_work
          ? [{ id: b.id, title: b.title, version_type: b.version_type, link: "confirmed" }]
          : [];
      }
      persist();
      return HttpResponse.json<Card>({ ...c, select_blocked_reason: selectBlocked(c) });
    },
  ),
  http.post<never, Schemas["LookupQuery"]>("/api/publications/lookup", async ({ request }) => {
    await delay(300);
    const q = (await request.json()).query.trim().toLowerCase();
    const listed = state.cards.map<Schemas["LookupCandidate"]>((c) => ({
      title: c.title,
      year: c.year,
      doi: c.doi,
      pmid: null,
      already_listed: true,
    }));
    const found = [...CATALOG, ...listed].filter((c) =>
      [c.title, c.doi, c.pmid].some((field) => field?.toLowerCase().includes(q)),
    );
    return HttpResponse.json<Schemas["LookupCandidate"][]>(q.length >= 3 ? found.slice(0, 5) : []);
  }),
  http.post<never, Schemas["ManualAddition"]>("/api/publications/manual", async ({ request }) => {
    const { doi, pmid } = await request.json();
    const candidate = CATALOG.find((c) => (doi && c.doi === doi) || (pmid && c.pmid === pmid));
    if (!candidate) return HttpResponse.json({ detail: "Запись не найдена" }, { status: 404 });
    const added = card({
      id: `manual-${Date.now()}`,
      title: candidate.title,
      year: candidate.year,
      doi: candidate.doi,
      authors: "Демо · C. Researcher",
      decision: "needs_review",
      open_access: "unknown",
      sources: [
        { resource: "manual", identifier: candidate.doi ?? candidate.pmid ?? "", url: null },
      ],
    });
    setDecision(
      added,
      "needs_review",
      "user",
      "Добавлено вручную: проверьте соответствие критериям.",
    );
    candidate.already_listed = true;
    state.cards.unshift(added);
    persist();
    return HttpResponse.json<Row>(row(added));
  }),
  http.post<never, never, Schemas["Problem"] | Schemas["ProceedResult"]>(
    "/api/selection/proceed",
    () => {
      const blocked = selection().proceed_blocked_reason;
      if (blocked) {
        return HttpResponse.json<Schemas["Problem"]>(
          { target: "selection", message: blocked },
          { status: 409 },
        );
      }
      studyDemo.reach("preparation");
      return HttpResponse.json<Schemas["ProceedResult"]>({ reached_step: "preparation" });
    },
  ),
];

/** Выбранные для извлечения работы — вход демо-хранилища документов. */
export const selectedWorks = () =>
  state.cards.filter((c) => c.selected).map(({ id, title }) => ({ id, title }));

/** Число найденных записей и фактические запросы — вход демо-результата. */
export const foundCount = () => state.cards.length;
export const searchQueries = () =>
  studyDemo.criteria().resources.map((resource) => ({ resource, query: query(resource) }));
