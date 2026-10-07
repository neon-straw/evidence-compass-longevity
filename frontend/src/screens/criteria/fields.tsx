import * as Popover from "@radix-ui/react-popover";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import type { ArticleType, Condition, PublicationKind, Schemas } from "../../api/types";

export const control =
  "w-full rounded-control border border-line bg-surface px-3 py-1 outline-none focus:border-accent focus:ring-2 focus:ring-accent-soft";

function joinRu(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} и ${items.at(-1)}`;
}

function PopoverField({
  label,
  summary,
  children,
}: {
  label: string;
  summary: string;
  children: ReactNode;
}) {
  return (
    <Popover.Root>
      <Popover.Trigger
        aria-label={`${label}: ${summary}`}
        className={`${control} flex items-center justify-between text-left`}
      >
        {summary}
        <ChevronDown aria-hidden className="size-4 text-ink-muted" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          className="z-20 w-(--radix-popover-trigger-width) rounded-control border border-line bg-surface p-3 shadow-md"
        >
          {children}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function PeriodField({
  value,
  onChange,
}: {
  value: Schemas["YearRange"];
  onChange: (value: Schemas["YearRange"]) => void;
}) {
  const { from_year, to_year } = value;
  const summary =
    from_year || to_year ? `${from_year ?? "…"}–${to_year ?? "…"}` : "без ограничения";
  const year = (raw: string) => (raw === "" ? null : Number(raw));
  return (
    <PopoverField label="Период публикации" summary={summary}>
      <div className="flex items-center gap-2">
        <input
          aria-label="Год с"
          type="number"
          className={control}
          value={from_year ?? ""}
          onChange={(event) => onChange({ ...value, from_year: year(event.target.value) })}
        />
        <span aria-hidden>–</span>
        <input
          aria-label="Год по"
          type="number"
          className={control}
          value={to_year ?? ""}
          onChange={(event) => onChange({ ...value, to_year: year(event.target.value) })}
        />
      </div>
      <button
        type="button"
        className="mt-2 text-accent hover:text-accent-strong"
        onClick={() => onChange({ from_year: null, to_year: null })}
      >
        Без ограничения
      </button>
    </PopoverField>
  );
}

/** Множественный выбор; пустой выбор — без ограничения по этому признаку. */
function MultiChoiceField<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: Record<T, string>;
  value: T[];
  onChange: (value: T[]) => void;
}) {
  const keys = Object.keys(options) as T[];
  const summary = value.length
    ? joinRu(keys.filter((key) => value.includes(key)).map((key) => options[key]))
    : "без ограничения";
  return (
    <PopoverField label={label} summary={summary}>
      <fieldset className="space-y-2">
        <legend className="sr-only">{label}</legend>
        {keys.map((key) => (
          <label key={key} className="flex items-center gap-2">
            <input
              type="checkbox"
              className="size-4 accent-accent"
              checked={value.includes(key)}
              onChange={(event) =>
                onChange(
                  event.target.checked ? [...value, key] : value.filter((item) => item !== key),
                )
              }
            />
            {options[key]}
          </label>
        ))}
      </fieldset>
    </PopoverField>
  );
}

export function ArticleTypesField(props: {
  value: ArticleType[];
  onChange: (value: ArticleType[]) => void;
}) {
  return (
    <MultiChoiceField
      label="Тип публикации"
      options={{
        primary_study: "первичные исследования",
        review: "обзоры",
        meta_analysis: "метаанализы",
        other: "другие",
      }}
      {...props}
    />
  );
}

export function PublicationKindsField(props: {
  value: PublicationKind[];
  onChange: (value: PublicationKind[]) => void;
}) {
  return (
    <MultiChoiceField
      label="Вид публикации"
      options={{ journal_article: "статьи", preprint: "препринты", other: "другие" }}
      {...props}
    />
  );
}

const CONDITIONS: { label: string; value: Condition[] }[] = [
  { label: "природа и неволя", value: ["captive", "wild"] },
  { label: "только природа", value: ["wild"] },
  { label: "только неволя", value: ["captive"] },
];

export function ConditionsField({
  value,
  onChange,
}: {
  value: Condition[];
  onChange: (value: Condition[]) => void;
}) {
  const key = (items: Condition[]) => [...items].sort().join("+");
  const current = key(value);
  const known = CONDITIONS.some((option) => key(option.value) === current);
  return (
    <div className="relative">
      <select
        aria-label="Условия"
        className={`${control} appearance-none pr-9`}
        value={known ? current : ""}
        onChange={(event) =>
          onChange(CONDITIONS.find((option) => key(option.value) === event.target.value)!.value)
        }
      >
        {!known && (
          <option value="" disabled>
            не выбраны
          </option>
        )}
        {CONDITIONS.map((option) => (
          <option key={key(option.value)} value={key(option.value)}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-ink-muted"
      />
    </div>
  );
}
