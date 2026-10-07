import type { Schemas } from "../../api/types";
import { plural } from "../../ui/plural";
import type { Tone } from "../../ui/StatusPill";

export type Status = Schemas["DocumentRow"]["status"];

export const EXTRACTION_KEY = ["extraction"] as const;

/** Операция занимает общий слот: документы доступны только для чтения (shape §2). */
export const ACTIVE: Schemas["Operation"]["status"][] = [
  "queued",
  "running",
  "waiting_llm",
  "pause_requested",
  "paused",
  "terminate_requested",
];

export const STATUS: Record<Status, { tone: Tone; label: string }> = {
  fetching: { tone: "progress", label: "Загружается" },
  ready: { tone: "success", label: "Готов" },
  not_found: { tone: "warning", label: "Не найден" },
  needs_review: { tone: "warning", label: "Требует проверки" },
  error: { tone: "warning", label: "Ошибка" },
  // Изображение, код, служебная форма: не читается, но это не пробел (shape §3.5).
  not_parsed: { tone: "neutral", label: "Не читается" },
};

type Work = Schemas["DocumentWork"];

/** Состояние работы до запуска по её основному тексту (shape §3.5). */
export const WORK_STATE: Record<Work["state"], { tone: Tone; label: string }> = {
  fetching: { tone: "progress", label: "Документы загружаются" },
  ready: { tone: "success", label: "Готова к извлечению" },
  no_full_text: { tone: "warning", label: "Нет полного текста" },
  needs_review: { tone: "warning", label: "Требует проверки" },
};

const measures = (n: number) => plural(n, ["показатель", "показателя", "показателей"]);

/** Итог обработки работы: выполнение и итог показываются раздельно (spec «Отбор и состояния»). */
export function workRun(run: Schemas["WorkRun"]): { tone: Tone; label: string } {
  if (run.execution === "queued") return { tone: "neutral", label: "В очереди" };
  if (run.execution === "running") return { tone: "progress", label: "Обрабатывается" };
  if (run.execution === "skipped") return { tone: "neutral", label: "Не обработана" };
  switch (run.outcome) {
    case "measures_extracted":
      return { tone: "success", label: measures(run.measure_count ?? 0) };
    case "no_measures":
      return { tone: "neutral", label: "Без показателей" };
    case "unavailable":
      return { tone: "neutral", label: "Документ недоступен" };
    case "unresolved":
      return { tone: "warning", label: "Требует решения" };
    case "error":
      return { tone: "warning", label: "Ошибка" };
    default:
      // Не обработана по причине, которую называет detail.
      return { tone: "warning", label: "Не обработана" };
  }
}

/** Работа раскрыта по умолчанию, если пользователю есть что делать; загрузка — не повод. */
export const needsAttention = (work: Work) =>
  work.state === "no_full_text" || work.state === "needs_review" || work.gaps.length > 0;

type Doc = Schemas["DocumentRow"];

/** Раздельные статусы документа (spec «Отбор и состояния»): загрузка, идентичность, чтение. */
export const FETCH: Record<NonNullable<Doc["fetch_status"]>, string> = {
  pending: "в очереди",
  fetching: "идёт",
  available: "загружен",
  unavailable: "не найден",
  error: "ошибка",
};

export const IDENTITY: Record<NonNullable<Doc["identity_status"]>, string> = {
  pending: "не проверена",
  confirmed: "подтверждена",
  needs_review: "требует проверки",
  mismatch: "не совпадает",
};

export const PARSE: Record<NonNullable<Doc["parse_status"]>, string> = {
  pending: "ожидает",
  parsing: "идёт",
  parsed: "прочитан",
  partial: "частично",
  unsupported: "формат не читается",
  error: "ошибка",
};

export function facets(doc: Doc): string {
  // Приложение, добавленное пользователем, из источника не загружалось.
  const parts = doc.fetch_status ? [`загрузка: ${FETCH[doc.fetch_status]}`] : ["загружен вручную"];
  if (doc.identity_status) parts.push(`идентичность: ${IDENTITY[doc.identity_status]}`);
  if (doc.parse_status) parts.push(`чтение: ${PARSE[doc.parse_status]}`);
  const text = parts.join(" · ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
