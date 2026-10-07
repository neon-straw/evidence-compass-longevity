import { HttpResponse, delay, http } from "msw";
import type { Schemas } from "../api/types";
import { plural } from "../ui/plural";
import { selectedWorks } from "./search";
import { appliedScenario, studyDemo } from "./study";

/**
 * Демо-состояние шага «Подготовка» (состояния 19–22 из docs/ux/stage-1-shape.md §9).
 * Имена файлов и число показателей — из data/corpus; статусы и ход операции — демо.
 * Операция развивается по времени: каждая работа обрабатывается WORK_MS; пауза и остановка
 * подтверждаются только после завершения текущей работы (безопасная граница).
 */
type Doc = Schemas["DocumentRow"];
type Work = Schemas["WorkRun"];
type Op = Schemas["Operation"];

// Раздельные статусы загрузки, идентичности и чтения выводятся из демо-статуса в `row`.
type Facets = "fetch_status" | "identity_status" | "parse_status" | "sha256" | "acquisition";
type DocState = Omit<Doc, "allowed_actions" | Facets | "source_url"> & {
  // Ссылка на PDF у источника, который не загрузился автоматически.
  source_url?: string;
  fetchUntil: number;
  afterFetch: Doc["status"];
  uploaded?: boolean;
  // Приложение, добавленное пользователем: из источника не загружалось, повторять загрузку неоткуда.
  added?: boolean;
};
type State = {
  documents: DocState[];
  status: Op["status"];
  works: Work[];
  currentStartedAt: number;
  retried: boolean;
  // Ключ идемпотентности последнего запуска и его вход.
  startKey: string | null;
  startInput: string | null;
  log: Schemas["LogEvent"][];
};

const STORAGE_KEY = "ec-demo-extraction";
const WORK_MS = 1200;
const QUEUE_MS = 300;
const FETCH_MS = 1000;

type Corpus = { label: string; measures: number; docs: [string, Doc["role"], number][] };

const CORPUS: Record<string, Corpus> = {
  ruby2018: {
    label: "Ruby 2018 · Heterocephalus glaber",
    measures: 3,
    docs: [
      ["ruby2018.pdf", "main", 2],
      ["ruby-supp1.csv", "supplement", 1],
      ["Демо · elife-31157-fig1.jpg", "supplement", 0],
      ["Демо · elife-31157-fig2.tif", "supplement", 0],
    ],
  },
  nielsen2016: {
    label: "Nielsen 2016 · Somniosus microcephalus",
    measures: 29,
    docs: [["shark-complete.pdf", "main", 1]],
  },
  podlutsky2005: {
    label: "Podlutsky 2005 · Myotis brandtii",
    measures: 1,
    docs: [["podlutsky2005.pdf", "main", 1]],
  },
  clubb2008: {
    label: "Clubb 2008 · Loxodonta africana",
    measures: 3,
    docs: [
      ["elephant-published.pdf", "main", 1],
      ["Демо · clubb2008-tables-S1.xlsx", "supplement", 0],
    ],
  },
};

const scenario = () => studyDemo.scenario();
const IMAGE = /\.(jpe?g|png|tiff?|gif|webp)$/i;
// Как на сервере: непрочитанное приложение этих форматов — пробел с подсказкой (shape §3.5).
const TABULAR = /\.(docx?|xlsx?|ods|odt|rtf|zip)$/i;
const READABLE = /\.(pdf|n?xml|csv|tsv)$/i;
const tabularHint = (name: string) =>
  `Формат ${name.split(".").pop()!.toUpperCase()} не читается: сохраните таблицу как CSV или PDF и загрузите новой версией.`;
/** Как `_unreadable` на сервере: приложение этого формата не загружается, причина видна сразу. */
const unreadable = (name: string) =>
  READABLE.test(name)
    ? null
    : IMAGE.test(name)
      ? "Изображение: значения из него не извлекаются."
      : TABULAR.test(name)
        ? tabularHint(name)
        : `Формат ${name.split(".").pop()!.toUpperCase()} не читается: поддерживаются PDF, JATS XML и CSV.`;

function initialDocuments(): DocState[] {
  const gaps = scenario() === "extraction-gaps";
  return selectedWorks().flatMap((work, n) => {
    const corpus = CORPUS[work.id] ?? {
      label: work.title,
      measures: 2,
      docs: [[`Демо · Основной текст ${n + 1}.pdf`, "main", 1]] as Corpus["docs"],
    };
    return corpus.docs.map(([name, role, version], i) => {
      let status: Doc["status"] = "ready";
      let detail: string | null = null;
      let source_url: string | undefined;
      const reason = role === "supplement" ? unreadable(name) : null;
      if (reason) {
        status = "not_parsed";
        detail = reason;
      } else if (version === 0) {
        status = "not_found";
        detail = "Приложение не найдено у издателя и в Europe PMC.";
      } else if (work.id === "podlutsky2005" && !studyDemo.startsAt("result")) {
        status = "needs_review";
        detail = "Файл загружен вручную: подтвердите, что это статья выбранной работы.";
      } else if (gaps && work.id === "clubb2008" && role === "main") {
        // Закрытая работа: PDF в PMC открывается только в браузере — путь в ручную загрузку.
        status = "not_found";
        detail =
          "Полный текст не загружен: Europe PMC — открытого полного текста нет; " +
          "Unpaywall — PMC отдаёт этот PDF только в браузере.";
        source_url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC0000000/pdf/elephant-published.pdf";
      } else if (gaps && work.id === "ruby2018" && role === "supplement") {
        status = "error";
        detail = "CSV не прочитан: неизвестная кодировка файла.";
      } else if (gaps && work.id === "nielsen2016") {
        status = "fetching";
      }
      return {
        id: `${work.id}-${i}`,
        name,
        publication_id: work.id,
        publication_label: corpus.label,
        role,
        // Не найденный и нечитаемый документ версии не имеют: файл не загружался.
        version: status === "not_found" || status === "not_parsed" ? null : version || null,
        status,
        status_detail: detail,
        source_url,
        fetchUntil: status === "fetching" ? Date.now() + 2 * FETCH_MS : 0,
        afterFetch: "ready",
      };
    });
  });
}

const workLabel = (id: string) =>
  CORPUS[id]?.label ?? selectedWorks().find((w) => w.id === id)?.title ?? id;

function note(s: State, message: string) {
  s.log.push({ at: new Date().toISOString(), message });
}

const mainDoc = (s: State, publicationId: string) =>
  s.documents.find((d) => d.publication_id === publicationId && d.role === "main");

/** Работы, которые можно обработать сейчас: основной текст готов, результата ещё нет. */
function processable(s: State): string[] {
  const done = new Set(s.works.filter((w) => w.execution === "done").map((w) => w.publication_id));
  return [...new Set(s.documents.map((d) => d.publication_id))].filter(
    (id) => mainDoc(s, id)?.status === "ready" && !done.has(id),
  );
}

function notProcessed(s: State, id: string): Work {
  const main = mainDoc(s, id);
  const needsReview = main?.status === "needs_review";
  return {
    publication_id: id,
    label: workLabel(id),
    execution: "skipped",
    // Непроверенная принадлежность — не то же, что недоступный документ (shape §5).
    outcome: needsReview ? null : "unavailable",
    measure_count: null,
    detail: needsReview
      ? "Основной текст требует проверки: подтвердите принадлежность файла и запустите работу."
      : "Основной текст не загружен: работа не обработана.",
    gaps: [],
  };
}

/** Запуск или дозапуск: готовые результаты сохраняются, в очередь встают только необработанные. */
function start(s: State, ids: string[]) {
  const queued = new Set(ids);
  const all = [...new Set(s.documents.map((d) => d.publication_id))];
  s.works = all.map((id) => {
    const existing = s.works.find((w) => w.publication_id === id);
    if (existing?.execution === "done") return existing;
    if (queued.has(id)) {
      return {
        publication_id: id,
        label: workLabel(id),
        execution: "queued",
        outcome: null,
        measure_count: null,
        detail: null,
        gaps: [],
      };
    }
    return notProcessed(s, id);
  });
  s.status = "queued";
  s.currentStartedAt = Date.now() + QUEUE_MS;
  note(s, `Извлечение поставлено в очередь для ${ids.length} работ.`);
}

function nextWork(s: State) {
  const next = s.works.find((w) => w.execution === "queued");
  if (next) next.execution = "running";
  return next;
}

function gapsOf(s: State, id: string): string[] {
  return s.documents
    .filter((d) => d.publication_id === id && d.role === "supplement" && d.status !== "ready")
    .map((d) => `${d.name}: не загружено, сведения из него не извлечены.`);
}

function finishCurrent(s: State) {
  const work = s.works.find((w) => w.execution === "running");
  if (!work) return;
  const partial = ["extraction-partial", "result-partial"].includes(scenario()) && !s.retried;
  const measures = CORPUS[work.publication_id]?.measures ?? 2;
  work.gaps = gapsOf(s, work.publication_id);
  const noMeasures = ["extraction-no-measures", "result-empty"].includes(scenario());
  if (noMeasures || (partial && work.publication_id === "clubb2008")) {
    Object.assign(work, {
      execution: "done",
      outcome: "no_measures",
      measure_count: 0,
      detail: "Опубликованные показатели продолжительности жизни в тексте не найдены.",
    });
  } else if (scenario() === "result-unresolved" && work.publication_id === "podlutsky2005") {
    Object.assign(work, {
      execution: "done",
      outcome: "unresolved",
      measure_count: 0,
      detail: "Демо: обзор не уточняет, к какой особи или группе относится оценка 40 ± 3 years.",
    });
  } else if (partial && work.publication_id === "nielsen2016") {
    Object.assign(work, {
      execution: "failed",
      outcome: "error",
      measure_count: null,
      detail: "Модель не ответила после 3 повторов. Готовые результаты других работ сохранены.",
    });
  } else {
    Object.assign(work, {
      execution: "done",
      outcome: "measures_extracted",
      measure_count: measures,
      detail: null,
    });
  }
  note(s, `${work.label}: ${work.detail ?? `извлечено показателей — ${measures}`}`);
  s.currentStartedAt += WORK_MS;
  // Сохранённые строки уже можно читать: шаг «Результат» становится доступен.
  if (work.execution === "done") studyDemo.reach("result");
}

/** Продвигает операцию по прошедшему времени. */
function advance(s: State) {
  const now = Date.now();
  for (const doc of s.documents) {
    if (doc.status === "fetching" && now >= doc.fetchUntil) {
      doc.status = doc.afterFetch;
      doc.status_detail =
        doc.afterFetch === "not_found" ? "Повторная попытка: файл по-прежнему недоступен." : null;
      if (doc.afterFetch === "ready" && !doc.version) doc.version = 1;
    }
  }
  if (s.status === "queued" && now >= s.currentStartedAt) {
    s.status = "running";
    nextWork(s);
    note(s, "Извлечение запущено.");
  }
  const inFlight = ["running", "pause_requested", "terminate_requested"];
  while (inFlight.includes(s.status) && now - s.currentStartedAt >= WORK_MS) {
    finishCurrent(s);
    if (s.status === "pause_requested") {
      s.status = "paused";
      note(s, "Пауза подтверждена после завершения работы.");
      break;
    }
    if (s.status === "terminate_requested") {
      for (const w of s.works.filter((w) => w.execution === "queued")) {
        Object.assign(w, {
          execution: "skipped",
          detail: "Не обработана: извлечение остановлено.",
        });
      }
      s.status = "terminated";
      note(s, "Извлечение остановлено после завершения текущей работы.");
      break;
    }
    if (!nextWork(s)) {
      s.status = "completed";
      note(s, "Извлечение завершено.");
    }
  }
}

const ACTIVE: Op["status"][] = [
  "queued",
  "running",
  "waiting_llm",
  "pause_requested",
  "paused",
  "terminate_requested",
];

function operation(s: State): Op {
  const counted = s.works.filter((w) => w.execution !== "skipped");
  const finished = counted.filter((w) => w.execution === "done" || w.execution === "failed");
  const actions: Op["allowed_actions"] = [];
  if (!ACTIVE.includes(s.status) && processable(s).length > 0) actions.push("start");
  if (s.status === "running") actions.push("pause", "stop");
  if (s.status === "queued" || s.status === "pause_requested") actions.push("stop");
  if (s.status === "paused" || s.status === "waiting_llm") actions.push("resume", "stop");
  if (!ACTIVE.includes(s.status) && s.works.some((w) => w.execution === "failed")) {
    actions.push("retry_failed");
  }
  return {
    status: s.status,
    progress: counted.length ? finished.length / counted.length : 0,
    works: s.works,
    allowed_actions: actions,
  };
}

const DEMO_HASH = (name: string) =>
  [...name]
    .reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7)
    .toString(16)
    .padStart(64, "0");

function facets(d: DocState): Pick<Doc, Facets> {
  const version = d.version !== null;
  const byStatus: Record<
    Doc["status"],
    Pick<Doc, "fetch_status" | "identity_status" | "parse_status">
  > = {
    fetching: { fetch_status: "fetching", identity_status: null, parse_status: null },
    ready: { fetch_status: "available", identity_status: "confirmed", parse_status: "parsed" },
    not_found: { fetch_status: "unavailable", identity_status: null, parse_status: null },
    needs_review: {
      fetch_status: "available",
      identity_status: "needs_review",
      parse_status: "parsed",
    },
    // Без версии — формат не загружался; с версией — загружен, но не прочитан.
    not_parsed: version
      ? { fetch_status: "available", identity_status: "confirmed", parse_status: "unsupported" }
      : { fetch_status: "unavailable", identity_status: null, parse_status: null },
    // Демо-ошибки: непрочитанный CSV или файл чужой работы.
    error: {
      fetch_status: "available",
      identity_status: d.status_detail?.startsWith("Файл не относится") ? "mismatch" : "confirmed",
      parse_status: d.status_detail?.startsWith("Файл не относится") ? "parsed" : "error",
    },
  };
  const found = byStatus[d.status];
  return {
    ...found,
    fetch_status: d.added ? null : found.fetch_status,
    identity_status: version ? (found.identity_status ?? "confirmed") : null,
    parse_status: version ? (found.parse_status ?? "parsed") : null,
    sha256: version ? DEMO_HASH(`${d.name}-${d.version}`) : null,
    acquisition: version ? (d.uploaded ? "uploaded" : "fetched") : null,
  };
}

function row(s: State, d: DocState): Doc {
  const { fetchUntil, afterFetch, uploaded, ...doc } = d;
  void [fetchUntil, afterFetch, uploaded];
  const actions: Doc["allowed_actions"] = [];
  if (!ACTIVE.includes(s.status) && d.status !== "fetching") {
    if (facets(d).fetch_status !== null && d.status !== "not_parsed") actions.push("retry_fetch");
    actions.push("upload");
    if (d.status === "needs_review") actions.push("confirm_identity");
  }
  return { ...doc, source_url: d.source_url ?? null, ...facets(d), allowed_actions: actions };
}

function extraction(): Schemas["Extraction"] {
  // До запуска документы следуют за выбором: пользователь мог вернуться к отбору.
  const selected = selectedWorks()
    .map((w) => w.id)
    .join();
  const listed = [...new Set(state.documents.map((d) => d.publication_id))].join();
  if (state.status === "not_started" && selected !== listed) state.documents = initialDocuments();
  advance(state);
  persist();
  const startable = processable(state);
  const ready = startable.length;
  const documents = state.documents.map((d) => row(state, d));
  return {
    documents,
    works: works(documents),
    startable_publication_ids: startable,
    operation: operation(state),
    start_blocked_reason: ACTIVE.includes(state.status)
      ? "Извлечение уже выполняется."
      : ready > 0
        ? null
        : state.status === "not_started"
          ? "Нет работ с готовым основным текстом: дождитесь загрузки или загрузите документы вручную."
          : "Все работы с готовым основным текстом уже обработаны.",
  };
}

/** Строки работ: состояние по основному тексту и пробелы приложений, как на сервере. */
function works(documents: Doc[]): Schemas["DocumentWork"][] {
  const ids = [...new Set(documents.map((d) => d.publication_id))];
  return ids.map((id) => {
    const own = documents.filter((d) => d.publication_id === id);
    const main = own.find((d) => d.role === "main");
    const workState: Schemas["DocumentWork"]["state"] =
      !main || main.status === "fetching"
        ? "fetching"
        : main.version === null
          ? "no_full_text"
          : // Как `_usable` на сервере: частично прочитанный основной текст тоже пригоден.
            main.identity_status === "confirmed" &&
              (main.parse_status === "parsed" || main.parse_status === "partial")
            ? "ready"
            : "needs_review";
    const supplements = own.filter((d) => d.role === "supplement");
    const count = (test: (d: Doc) => boolean, one: string, many: string) => {
      const n = supplements.filter(test).length;
      if (!n) return [];
      const noun = plural(n, ["приложение", "приложения", "приложений"]);
      return [`${noun} ${noun.endsWith("приложение") ? one : many}`];
    };
    const unsupported = (d: Doc) => d.status === "not_parsed" && TABULAR.test(d.name);
    return {
      publication_id: id,
      label: own[0].publication_label,
      document_count: own.length,
      state: workState,
      gaps: [
        ...count((d) => d.status === "not_found", "не загружено", "не загружены"),
        ...count((d) => unsupported(d), "с таблицами не прочитано", "с таблицами не прочитаны"),
        ...count((d) => d.status === "needs_review", "требует проверки", "требуют проверки"),
        ...count((d) => d.status === "error", "с ошибкой", "с ошибкой"),
      ],
      allowed_actions: ACTIVE.includes(state.status) ? [] : ["upload_supplement"],
    };
  });
}

function withDoc(id: string, change: (d: DocState) => void) {
  const doc = state.documents.find((d) => d.id === id);
  if (!doc) return HttpResponse.json({ detail: "Документ не найден" }, { status: 404 });
  if (ACTIVE.includes(state.status)) {
    return HttpResponse.json(
      { detail: "Документы нельзя менять, пока извлечение выполняется или на паузе." },
      { status: 409 },
    );
  }
  change(doc);
  persist();
  return HttpResponse.json<Doc>(row(state, doc));
}

function command(change: (s: State) => void) {
  advance(state);
  change(state);
  persist();
  return HttpResponse.json<Op>(operation(state));
}

function initialState(): State {
  const s: State = {
    documents: initialDocuments(),
    status: "not_started",
    works: [],
    currentStartedAt: 0,
    retried: false,
    startKey: null,
    startInput: null,
    log: [],
  };
  if (studyDemo.startsAt("result")) {
    // Результат открывают после завершённого извлечения: прогоняем все готовые работы.
    start(s, processable(s));
    s.status = "running";
    while (nextWork(s)) finishCurrent(s);
    s.status = "completed";
    note(s, "Извлечение завершено.");
  } else if (scenario() === "extraction-paused" || scenario() === "extraction-waiting-llm") {
    start(s, processable(s));
    s.status = "running";
    nextWork(s);
    finishCurrent(s);
    nextWork(s);
    if (scenario() === "extraction-paused") {
      s.status = "paused";
      note(s, "Пауза подтверждена после завершения работы.");
    } else {
      s.status = "waiting_llm";
      note(s, "Модель не ответила в пределах бюджета повторов: ожидается явное продолжение.");
    }
  }
  return s;
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

export const extractionHandlers = [
  http.get("/api/extraction", () => HttpResponse.json<Schemas["Extraction"]>(extraction())),
  http.post<{ id: string }>("/api/documents/:id/retry-fetch", ({ params }) =>
    withDoc(params.id, (d) => {
      d.afterFetch = d.status === "not_found" ? "not_found" : "ready";
      d.status = "fetching";
      d.status_detail = null;
      d.fetchUntil = Date.now() + FETCH_MS;
      note(state, `${d.name}: повторная загрузка.`);
    }),
  ),
  http.post<{ id: string }>("/api/documents/:id/upload", async ({ params, request }) => {
    const file = (await request.formData()).get("file");
    await delay(200);
    return withDoc(params.id, (d) => {
      d.name = file instanceof File ? file.name : d.name;
      d.version = (d.version ?? 0) + 1;
      const reason = unreadable(d.name);
      d.status = reason ? "not_parsed" : "ready";
      d.status_detail = reason ?? "Загружено вручную.";
      d.uploaded = true;
      note(state, `${d.name}: загружена версия v${d.version}.`);
    });
  }),
  http.post<{ id: string }>("/api/publications/:id/supplements", async ({ params, request }) => {
    const file = (await request.formData()).get("file");
    await delay(200);
    const main = mainDoc(state, params.id);
    if (!main) return HttpResponse.json({ detail: "Работа не найдена" }, { status: 404 });
    if (ACTIVE.includes(state.status)) {
      return HttpResponse.json(
        { detail: "Документы нельзя менять, пока извлечение выполняется или на паузе." },
        { status: 409 },
      );
    }
    const name = file instanceof File ? file.name : "Приложение";
    const doc: DocState = {
      id: `${params.id}-added-${state.documents.length}`,
      name,
      publication_id: params.id,
      publication_label: main.publication_label,
      role: "supplement",
      version: 1,
      status: "needs_review",
      status_detail: "Файл загружен вручную: подтвердите, что это приложение выбранной работы.",
      fetchUntil: 0,
      afterFetch: "ready",
      uploaded: true,
      added: true,
    };
    state.documents.push(doc);
    note(state, `${name}: добавлено приложение.`);
    persist();
    return HttpResponse.json<Doc>(row(state, doc));
  }),
  http.post<{ id: string }, Schemas["IdentityDecision"]>(
    "/api/documents/:id/identity",
    async ({ params, request }) => {
      const { belongs_to_publication } = await request.json();
      return withDoc(params.id, (d) => {
        d.status = belongs_to_publication ? "ready" : "error";
        d.status_detail = belongs_to_publication
          ? "Принадлежность подтверждена пользователем."
          : "Файл не относится к работе: загрузите верный документ.";
      });
    },
  ),
  http.post<never, Schemas["StartExtraction"], Schemas["Problem"] | Op>(
    "/api/extraction/start",
    async ({ request }) => {
      const key = request.headers.get("idempotency-key");
      const body = await request.json();
      const input = JSON.stringify(body);
      advance(state);
      if (key && key === state.startKey) {
        // Повтор той же команды возвращает ту же операцию; другой вход с тем же ключом — конфликт.
        if (input === state.startInput) return HttpResponse.json<Op>(operation(state));
        return HttpResponse.json<Schemas["Problem"]>(
          { target: "idempotency_key", message: "Ключ запуска уже использован с другим входом." },
          { status: 409 },
        );
      }
      const allowed = processable(state);
      if (!body.publication_ids.every((id) => allowed.includes(id))) {
        return HttpResponse.json<Schemas["Problem"]>(
          {
            target: "publication_ids",
            message: extraction().start_blocked_reason ?? "Состав работ изменился: обновите экран.",
          },
          { status: 409 },
        );
      }
      state.startKey = key;
      state.startInput = input;
      start(state, body.publication_ids);
      persist();
      return HttpResponse.json<Op>(operation(state), { status: 202 });
    },
  ),
  http.post("/api/extraction/pause", () =>
    command((s) => {
      if (s.status !== "running") return;
      s.status = "pause_requested";
      note(s, "Запрошена пауза: текущая работа завершается.");
    }),
  ),
  http.post("/api/extraction/resume", () =>
    command((s) => {
      if (s.status !== "paused" && s.status !== "waiting_llm") return;
      s.status = "running";
      s.currentStartedAt = Date.now();
      if (!s.works.some((w) => w.execution === "running")) nextWork(s);
      note(s, "Извлечение продолжено.");
    }),
  ),
  http.post("/api/extraction/stop", () =>
    command((s) => {
      if (["paused", "waiting_llm", "queued"].includes(s.status)) {
        for (const w of s.works.filter((w) => ["queued", "running"].includes(w.execution))) {
          Object.assign(w, {
            execution: "skipped",
            detail: "Не обработана: извлечение остановлено.",
          });
        }
        s.status = "terminated";
        note(s, "Извлечение остановлено.");
      } else if (s.status === "running" || s.status === "pause_requested") {
        s.status = "terminate_requested";
        note(s, "Запрошена остановка: текущая работа завершается.");
      }
    }),
  ),
  http.post("/api/extraction/retry-failed", () =>
    command((s) => {
      const failed = s.works.filter((w) => w.execution === "failed");
      if (!failed.length) return;
      for (const w of failed)
        Object.assign(w, { execution: "queued", outcome: null, detail: null });
      s.retried = true;
      s.status = "queued";
      s.currentStartedAt = Date.now() + QUEUE_MS;
      note(s, `Повтор неудачных работ: ${failed.length}. Готовые результаты не затронуты.`);
    }),
  ),
  http.get("/api/extraction/log", () => {
    // Операция идёт независимо от открытого экрана.
    advance(state);
    persist();
    return HttpResponse.json<Schemas["LogEvent"][]>(state.log);
  }),
];

/** Итоги работ последнего извлечения — вход демо-результата. */
export const workOutcomes = () => state.works;
