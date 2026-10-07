import { HttpResponse, delay, http } from "msw";
import type { Schemas } from "../api/types";

/**
 * Демо-состояние исследования для шагов «Вопрос» и «Критерии».
 * Хранится в sessionStorage, чтобы автосохранение переживало перезагрузку вкладки.
 * Сценарий (состояния 9–12 из docs/ux/stage-1-shape.md §9) выбирается `?scenario=`.
 *
 * Реальный клиент (без VITE_API_MOCK) для шага «Вопрос» ходит в API (#9).
 * Обработчики ниже остаются только в `dev:mock`, чтобы эти состояния жили без сервера.
 */
export type Scenario =
  | "default"
  | "empty-question"
  | "unsupported-question"
  | "prepare-failed"
  | "criteria-partial"
  | "criteria-large"
  // Начинают с подтверждённых критериев; исходы ресурсов задаёт mocks/search.ts.
  | "search-ready"
  | "search-limited"
  | "search-empty"
  | "search-partial"
  | "search-failed"
  // Начинают с завершённого поиска; состояние отбора задаёт mocks/search.ts.
  | "selection-default"
  | "selection-limit"
  | "selection-none-included"
  // Начинают с выбранных работ; документы и операцию задаёт mocks/extraction.ts.
  | "extraction-ready"
  | "extraction-gaps"
  | "extraction-paused"
  | "extraction-waiting-llm"
  | "extraction-partial"
  | "extraction-no-measures"
  // Начинают с завершённого извлечения; строки и ревизию задаёт mocks/result.ts.
  | "result-default"
  | "result-partial"
  | "result-empty"
  | "result-stale"
  | "result-unresolved";

type State = {
  scenario: Scenario;
  reachedStep: Schemas["Study"]["reached_step"];
  question: Schemas["QuestionDraft"];
  criteria: Schemas["CriteriaFields"] | null;
  criteriaSavedAt: string | null;
  confirmedVersion: number | null;
  // Снимок последней подтверждённой версии для признака неподтверждённых изменений.
  confirmedSnapshot: string | null;
  preparingUntil: number;
};

const STORAGE_KEY = "ec-demo-study";
const PREPARE_MS = 1200;

const DEMO_QUESTION =
  "Какие численные показатели продолжительности жизни опубликованы для выбранных видов животных?";

const DEMO_OBJECTS: [string, string][] = [
  ["Голый землекоп", "Heterocephalus glaber"],
  ["Гренландская акула", "Somniosus microcephalus"],
  ["Ночница Брандта", "Myotis brandtii"],
  ["Саванный слон", "Loxodonta africana"],
];

const LARGE_OBJECTS: [string, string][] = [
  ...DEMO_OBJECTS,
  ["Гренландский кит", "Balaena mysticetus"],
  ["Исландская циприна", "Arctica islandica"],
  ["Галапагосская черепаха", "Chelonoidis niger"],
  ["Длиннокрылый бахромчатый", "Myotis lucifugus"],
  ["Азиатский слон", "Elephas maximus"],
  ["Бурый медведь", "Ursus arctos"],
  ["Шимпанзе", "Pan troglodytes"],
  ["Горилла", "Gorilla gorilla"],
  ["Орангутан", "Pongo pygmaeus"],
  ["Лошадь", "Equus caballus"],
  ["Серый кит", "Eschrichtius robustus"],
  ["Касатка", "Orcinus orca"],
  ["Белуха", "Delphinapterus leucas"],
  ["Нарвал", "Monodon monoceros"],
  ["Гуттаперчевая черепаха", "Malacochersus tornieri"],
  ["Туатара", "Sphenodon punctatus"],
  ["Протей", "Proteus anguinus"],
  ["Серый попугай", "Psittacus erithacus"],
  ["Альбатрос Лейсана", "Phoebastria immutabilis"],
  ["Морской ёж красный", "Mesocentrotus franciscanus"],
];

const LIFESPAN_TERMS = ["lifespan", "longevity", "age estimate", "survival"];

function manyTerms(): string[] {
  const base = ["lifespan", "longevity", "life span", "maximum lifespan", "median lifespan"];
  const qualifiers = ["captive", "wild", "field", "zoo", "laboratory", "population", "individual"];
  const extra = ["age determination", "age estimation", "radiocarbon", "eye lens", "growth layer"];
  return [...base, ...qualifiers.flatMap((q) => base.map((b) => `${q} ${b}`)), ...extra].slice(
    0,
    110,
  );
}

type Criterion = Schemas["SearchCriterion"];

const suggested = (id: string, label: string, terms: string[]): Criterion => ({
  id,
  label,
  terms,
  suggested_terms: terms,
  origin: "suggested",
});

/** Состояние 12: 20+ критериев поискового запроса. */
function manyCriteria(): Criterion[] {
  const topics = ["survival", "mortality", "senescence", "growth", "maturity", "reproduction"];
  const methods = ["telomere", "radiocarbon", "mark-recapture", "skeletochronology"];
  return [...topics, ...methods].flatMap((topic, i) => [
    suggested(`crit-extra-${i}a`, `Тема: ${topic}`, [topic, `${topic} rate`]),
    suggested(`crit-extra-${i}b`, `Контекст: ${topic}`, [
      `${topic} in wild`,
      `${topic} in captivity`,
    ]),
  ]);
}

function emptyCriteria(): Schemas["CriteriaFields"] {
  return { ...suggestedCriteria("default"), objects: [], search_criteria: [] };
}

function suggestedCriteria(scenario: Scenario): Schemas["CriteriaFields"] {
  const objects = scenario === "criteria-large" ? LARGE_OBJECTS : DEMO_OBJECTS;
  return {
    objects: objects.map(([name, scientific_name], i) => ({
      id: `obj-${i + 1}`,
      name,
      // Частичный отказ: для одного вида научное название не предложено.
      scientific_name: scenario === "criteria-partial" && i === 2 ? "" : scientific_name,
      aliases:
        [["naked mole rat"], ["Greenland shark"], ["Brandt bat"], ["African elephants"]][i] ?? [],
      suggested_aliases:
        [["naked mole rat"], ["Greenland shark"], ["Brandt bat"], ["African elephants"]][i] ?? [],
      origin: "suggested",
    })),
    period: { from_year: 2000, to_year: 2026 },
    article_types: ["primary_study", "review"],
    publication_kinds: ["journal_article", "preprint"],
    conditions: ["wild", "captive"],
    search_criteria: [
      suggested(
        "crit-2",
        "Продолжительность жизни и возраст",
        scenario === "criteria-large" ? manyTerms() : LIFESPAN_TERMS,
      ),
      ...(scenario === "criteria-large" ? manyCriteria() : []),
    ],
    resources: ["pubmed", "europe_pmc"],
  };
}

/** Шаг, с которого начинается сценарий; `extraction-*` названы до переименования шага. */
function scenarioStep(scenario: Scenario): string {
  const prefix = scenario.split("-")[0];
  return prefix === "extraction" ? "preparation" : prefix;
}

function initialState(scenario: Scenario): State {
  const stage = scenarioStep(scenario);
  const started = ["search", "selection", "preparation", "result"].includes(stage);
  const criteria = started ? suggestedCriteria("default") : null;
  return {
    scenario,
    reachedStep: started
      ? (stage as "search" | "selection" | "preparation" | "result")
      : "question",
    question: {
      text: scenario === "empty-question" ? "" : DEMO_QUESTION,
      saved_at: scenario === "empty-question" ? null : new Date().toISOString(),
    },
    criteria,
    criteriaSavedAt: started ? new Date().toISOString() : null,
    confirmedVersion: started ? 1 : null,
    confirmedSnapshot: started ? JSON.stringify(criteria) : null,
    preparingUntil: 0,
  };
}

/** Сценарий, применённый при этой загрузке страницы; остальные демо-хранилища сбрасываются по нему. */
export let appliedScenario: Scenario | null = null;

function load(): State {
  const url = new URL(location.href);
  const requested = url.searchParams.get("scenario") as Scenario | null;
  if (requested) {
    // Сценарий применяется один раз: перезагрузка продолжает с сохранённого состояния.
    url.searchParams.delete("scenario");
    history.replaceState(history.state, "", url);
    appliedScenario = requested;
    return initialState(requested);
  }
  const stored = sessionStorage.getItem(STORAGE_KEY);
  return stored ? (JSON.parse(stored) as State) : initialState("default");
}

let state = load();
const persist = () => sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
persist();

const STEP_ORDER: Schemas["Study"]["reached_step"][] = [
  "question",
  "criteria",
  "search",
  "selection",
  "preparation",
  "result",
];

/** Доступ других демо-хранилищ к общему состоянию исследования. */
export const studyDemo = {
  scenario: () => state.scenario,
  /** Сценарий начинается на этом шаге или позже: предыдущие шаги уже пройдены. */
  startsAt(step: Schemas["Study"]["reached_step"]) {
    const stage = scenarioStep(state.scenario) as Schemas["Study"]["reached_step"];
    return STEP_ORDER.includes(stage) && STEP_ORDER.indexOf(stage) >= STEP_ORDER.indexOf(step);
  },
  question: () => state.question.text,
  criteria: () => state.criteria ?? suggestedCriteria("default"),
  confirmedVersion: () => state.confirmedVersion ?? 0,
  reach(step: Schemas["Study"]["reached_step"]) {
    if (STEP_ORDER.indexOf(step) > STEP_ORDER.indexOf(state.reachedStep)) {
      state.reachedStep = step;
      persist();
    }
  },
};

function problems(fields: Schemas["CriteriaFields"]): Schemas["Problem"][] {
  const found: Schemas["Problem"][] = [];
  if (fields.resources.length === 0) {
    found.push({ target: "resources", message: "Выберите хотя бы один ресурс поиска." });
  }
  for (const criterion of fields.search_criteria) {
    if (criterion.terms.length === 0) {
      const name = criterion.label || "без названия";
      found.push({
        target: criterion.id,
        message: `Критерий «${name}» нельзя представить в запросе: добавьте поисковые термины.`,
      });
    }
  }
  for (const object of fields.objects) {
    if ((object.aliases ?? []).some((alias) => /["()\[\]*?:\\]|\b(?:AND|OR|NOT)\b/.test(alias))) {
      found.push({
        target: object.id,
        message:
          "Поисковые названия содержат синтаксис запроса; укажите только названия и варианты написания без операторов и кавычек.",
      });
    }
  }
  return found;
}

function criteriaDraft(): Schemas["CriteriaDraft"] {
  const preparing = Date.now() < state.preparingUntil;
  const fields = state.criteria ?? suggestedCriteria(state.scenario);
  const partial = state.scenario === "criteria-partial";
  return {
    ...(preparing ? { ...fields, objects: [], search_criteria: [] } : fields),
    status: preparing ? "preparing" : partial ? "partial" : "ready",
    gaps:
      partial && !preparing
        ? ["Не получено научное название для «Ночница Брандта» — укажите его вручную."]
        : [],
    problems: preparing ? [] : problems(fields),
    confirmed_version: state.confirmedVersion,
    has_unconfirmed_changes: JSON.stringify(fields) !== state.confirmedSnapshot,
    allowed_actions: preparing
      ? []
      : problems(fields).length > 0
        ? ["reformulate"]
        : ["confirm", "reformulate"],
    saved_at: state.criteriaSavedAt,
  };
}

export const studyHandlers = [
  http.get("/api/study", () =>
    HttpResponse.json<Schemas["Study"]>({ reached_step: state.reachedStep }),
  ),
  http.get("/api/question", () => HttpResponse.json<Schemas["QuestionDraft"]>(state.question)),
  http.put("/api/question", async ({ request }) => {
    const { text } = (await request.json()) as Schemas["QuestionText"];
    await delay(150);
    state.question = { text, saved_at: new Date().toISOString() };
    persist();
    return HttpResponse.json<Schemas["QuestionDraft"]>(state.question);
  }),
  http.post<
    never,
    never,
    Schemas["UnsupportedQuestion"] | Schemas["ServiceProblem"] | Schemas["CriteriaDraft"]
  >("/api/question/prepare-criteria", async () => {
    await delay(200);
    if (state.scenario === "prepare-failed") {
      return HttpResponse.json<Schemas["ServiceProblem"]>(
        { message: "Модель не ответила за отведённое время." },
        { status: 503 },
      );
    }
    if (state.scenario === "unsupported-question") {
      return HttpResponse.json<Schemas["UnsupportedQuestion"]>(
        {
          message:
            "Stage 1 отвечает только на вопросы об опубликованных показателях возраста или продолжительности жизни животных. Этот вопрос требует другой формы результата.",
          example:
            "Какие численные показатели продолжительности жизни опубликованы для голого землекопа?",
        },
        { status: 422 },
      );
    }
    state.criteria = suggestedCriteria(state.scenario);
    state.criteriaSavedAt = new Date().toISOString();
    state.preparingUntil = Date.now() + PREPARE_MS;
    if (state.reachedStep === "question") state.reachedStep = "criteria";
    persist();
    return HttpResponse.json<Schemas["CriteriaDraft"]>(criteriaDraft(), { status: 202 });
  }),
  http.post("/api/criteria/start-manually", () => {
    state.criteria = emptyCriteria();
    state.criteriaSavedAt = new Date().toISOString();
    state.scenario = "default";
    if (state.reachedStep === "question") state.reachedStep = "criteria";
    persist();
    return HttpResponse.json<Schemas["CriteriaDraft"]>(criteriaDraft());
  }),
  http.get("/api/criteria", () => HttpResponse.json<Schemas["CriteriaDraft"]>(criteriaDraft())),
  http.put("/api/criteria", async ({ request }) => {
    state.criteria = (await request.json()) as Schemas["CriteriaFields"];
    state.criteriaSavedAt = new Date().toISOString();
    persist();
    await delay(150);
    return HttpResponse.json<Schemas["CriteriaDraft"]>(criteriaDraft());
  }),
  http.post("/api/criteria/reformulate", async () => {
    await delay(300);
    return HttpResponse.json<Schemas["CriteriaProposal"]>({
      search_criteria: suggestedCriteria(state.scenario).search_criteria,
    });
  }),
  http.post<never, never, Schemas["CriteriaVersion"] | Schemas["CriteriaDraft"]>(
    "/api/criteria/confirm",
    () => {
      const draft = criteriaDraft();
      if (draft.problems.length > 0 || draft.status === "preparing") {
        return HttpResponse.json<Schemas["CriteriaDraft"]>(draft, { status: 409 });
      }
      state.confirmedVersion = (state.confirmedVersion ?? 0) + 1;
      state.confirmedSnapshot = JSON.stringify(state.criteria);
      state.reachedStep = "search";
      persist();
      return HttpResponse.json<Schemas["CriteriaVersion"]>({ version: state.confirmedVersion });
    },
  ),
];
