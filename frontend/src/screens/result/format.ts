import { api } from "../../api/client";
import type { Schemas } from "../../api/types";
import type { Tone } from "../../ui/StatusPill";

type Row = Schemas["MeasureRow"];

export const RESULT_KEY = ["result"] as const;
export const evidenceKey = (id: string) => ["evidence", id] as const;

export const KIND: Record<Row["measure_kind"], string> = {
  mean: "Среднее",
  median: "Медиана",
  observed_maximum: "Наблюдаемый максимум",
  individual_age: "Возраст особи",
  modelled_age: "Модельная оценка возраста",
  survival_interval: "Интервал выживания",
  maturity_age: "Возраст созревания",
  other: "Другой показатель",
};

/** Проверка извлечения пользователем — не оценка качества исследования. */
export const REVIEW: Record<Row["review_status"], { tone: Tone; label: string }> = {
  unreviewed: { tone: "warning", label: "Требует проверки" },
  checked: { tone: "success", label: "Извлечение проверено" },
  corrected: { tone: "success", label: "Исправлено" },
  rejected: { tone: "neutral", label: "Отклонено" },
};

/** Сверка значения с документом: автоматическая или, если она не прошла, пользователем. */
export type Verification = "verified" | "checked_manually" | NonNullable<Row["verification_gap"]>;

export function verification(row: Row): Verification {
  if (!row.verification_gap) return "verified";
  return row.review_status === "checked" ? "checked_manually" : row.verification_gap;
}

/** Метка у значения; сверенное автоматически — без метки. Система знает только, что её
 * проверка не сошлась, — не что в источнике этого нет. */
export const VERIFICATION: Record<
  Exclude<Verification, "verified">,
  { tone: Tone; label: string }
> = {
  checked_manually: { tone: "neutral", label: "Сверено вручную" },
  quote_not_found: { tone: "warning", label: "Цитата не найдена" },
  value_not_matched: { tone: "warning", label: "Значение не сверено" },
};

export const VERIFICATION_FILTER = {
  all: "Любая",
  matched: "Сверено",
  unmatched: "Требует сверки",
} as const;
export type VerificationFilter = keyof typeof VERIFICATION_FILTER;

/** Сверено автоматически или пользователем. */
export const isMatched = (check: Verification) =>
  check === "verified" || check === "checked_manually";

export const matchesVerification = (row: Row, filter: VerificationFilter) =>
  filter === "all" || isMatched(verification(row)) === (filter === "matched");

/** Чей это результат. Значение по другим работам или известный факт (например, во введении)
 * — не результат работы, даже без ссылки рядом: в краткий вывод оно не входит. */
export const ORIGIN: Record<Row["origin_kind"], { label: string; detail: string | null }> = {
  own_result: { label: "Результат этой работы", detail: null },
  secondary_report: {
    label: "Не результат работы",
    detail:
      "значение приведено по другим работам или как известный факт; первоисточник не проверен, в краткий вывод строка не входит.",
  },
};

export const SETTING: Record<Row["conditions"]["setting"], string> = {
  wild: "природа",
  captive: "неволя",
  unknown: "условия не указаны",
};

export const rowLabel = (n: number) => String(n).padStart(2, "0");

function bounds(row: Row): string {
  const u = row.uncertainty;
  if (u?.plus_minus) return ` ± ${u.plus_minus}`;
  if (u && (u.lower || u.upper)) {
    const upper = u.upper ?? (u.upper_status === "not_reached" ? "не достигнута" : "не указана");
    return ` [${u.lower ?? "не указана"}; ${upper}]`;
  }
  return "";
}

/** Значение для таблицы: число, границы и единицы; оговорка — отметкой «*». */
export function valueText(row: Row): string {
  const qualifier = row.qualifier ? " *" : "";
  return `${row.value_original}${bounds(row)} ${row.unit_original}${qualifier}`;
}

/** Значение как опубликовано: оговорка словами источника, число, границы и единицы. */
export function publishedValue(row: Row): string {
  const value = `${row.value_original}${bounds(row)} ${row.unit_original}`;
  return row.qualifier ? `${row.qualifier} ${value}` : value;
}

export const precisionAndSpreadText = (row: Row) =>
  row.uncertainty?.label ?? "Нет извлечённых сведений";

/** Один запрос результата для экрана, охвата и кнопки экспорта: одинаковый кэш и ошибки. */
export const resultQuery = {
  queryKey: RESULT_KEY,
  queryFn: async () => {
    const { data } = await api.GET("/api/result");
    if (!data) throw new Error("Результат не загружен");
    return data;
  },
};

export type Correction = { value: string; unit: string; comment: string };
