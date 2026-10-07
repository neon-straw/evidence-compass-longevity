import { CircleAlert, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { Origin, Resource, Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import {
  ArticleTypesField,
  ConditionsField,
  PeriodField,
  PublicationKindsField,
  control,
} from "./fields";

type Fields = Schemas["CriteriaFields"];
type Criterion = Fields["search_criteria"][number];
type Update = (patch: Partial<Fields>) => void;

/** Порог, после которого длинные списки получают поиск и прокрутку (shape §6). */
const COMPACT_LIST = 8;
const COMPACT_TERMS = 15;

let nextId = 0;
const newId = (prefix: string) => `${prefix}-new-${Date.now()}-${nextId++}`;
const edited = (origin: Origin): Origin => (origin === "suggested" ? "edited" : origin);

export function Section({
  number,
  title,
  hint,
  action,
  children,
}: {
  number: number;
  title: string;
  hint: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={`section-${number}`} className="border-t border-line py-4">
      <div className="flex items-center gap-3">
        <span className="grid size-7 place-items-center rounded-full bg-neutral-soft text-caption font-semibold">
          {number}
        </span>
        <h2 id={`section-${number}`} className="text-lg font-semibold">
          {title}
        </h2>
        <p id={`section-${number}-hint`} className="text-caption text-ink-muted">
          {hint}
        </p>
        {action && <div className="ml-auto">{action}</div>}
      </div>
      <div className="mt-2 pl-10">{children}</div>
    </section>
  );
}

function Skeleton({ label }: { label: string }) {
  return (
    <div aria-busy="true" aria-label={label} className="space-y-1.5">
      <p className="text-ink-muted">Система готовит предложения по вопросу…</p>
      {[0, 1, 2].map((row) => (
        <div key={row} className="h-8 animate-pulse rounded-control bg-neutral-soft" />
      ))}
    </div>
  );
}

function OriginTag({ origin }: { origin: Origin }) {
  if (origin === "suggested") return null;
  return (
    <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-caption text-accent">
      {origin === "edited" ? "изменено" : "добавлено"}
    </span>
  );
}

/** Поиск и счётчик для длинного списка; короткий показывается как есть. */
function useCompactList<T>(items: T[], text: (item: T) => string) {
  const [filter, setFilter] = useState("");
  const compact = items.length > COMPACT_LIST;
  const query = filter.trim().toLowerCase();
  const visible = query ? items.filter((item) => text(item).toLowerCase().includes(query)) : items;
  return { compact, filter, setFilter, visible };
}

function ListFilter({
  label,
  count,
  filter,
  setFilter,
}: {
  label: string;
  count: string;
  filter: string;
  setFilter: (value: string) => void;
}) {
  return (
    <div className="mb-2 flex items-center gap-3">
      <input
        aria-label={label}
        placeholder={`${label}…`}
        className={`${control} max-w-sm`}
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />
      <span className="text-ink-muted">{count}</span>
    </div>
  );
}

export function ObjectsSection({
  objects,
  preparing,
  problems,
  update,
}: {
  objects: Fields["objects"];
  preparing: boolean;
  problems: Schemas["Problem"][];
  update: Update;
}) {
  const [aliasesText, setAliasesText] = useState<Record<string, string>>({});
  const list = useCompactList(
    objects,
    (o) => `${o.name} ${o.scientific_name} ${(o.aliases ?? []).join(" ")}`,
  );

  const change = (id: string, patch: Partial<Fields["objects"][number]>) =>
    update({
      objects: objects.map((o) => (o.id === id ? { ...o, ...patch, origin: edited(o.origin) } : o)),
    });

  return (
    <Section
      number={1}
      title="Объекты"
      hint="Укажите объекты (виды животных), для которых будут искать публикации."
    >
      {preparing ? (
        <Skeleton label="Объекты готовятся" />
      ) : (
        <>
          <p id="object-aliases-hint" className="mb-3 text-caption text-ink-muted">
            Поисковые названия на английском расширяют поиск: достаточно любого названия любого
            выбранного вида. Это не подтверждает тождество таксонов. Разделяйте варианты точкой с
            запятой.
          </p>
          {list.compact && (
            <ListFilter
              label="Найти объект"
              count={`Объектов: ${objects.length}`}
              filter={list.filter}
              setFilter={list.setFilter}
            />
          )}
          <ul className={`space-y-1.5 ${list.compact ? "max-h-80 overflow-y-auto pr-1" : ""}`}>
            {list.visible.map((object) => {
              const n = objects.indexOf(object) + 1;
              const problem = problems.find((p) => p.target === object.id);
              return (
                <li key={object.id} className="space-y-2 rounded-control border border-line p-3">
                  <div className="flex items-center gap-2">
                    <input
                      aria-label={`Название объекта ${n}`}
                      className={control}
                      value={object.name}
                      onChange={(event) => change(object.id, { name: event.target.value })}
                    />
                    <input
                      aria-label={`Научное название объекта ${n}`}
                      placeholder="Научное название"
                      className={control}
                      value={object.scientific_name}
                      onChange={(event) =>
                        change(object.id, { scientific_name: event.target.value })
                      }
                    />
                    <OriginTag origin={object.origin} />
                    <button
                      type="button"
                      aria-label={`Удалить объект ${n}${object.name ? ` «${object.name}»` : ""}`}
                      className="p-1 text-ink-muted hover:text-ink"
                      onClick={() => update({ objects: objects.filter((o) => o.id !== object.id) })}
                    >
                      <X aria-hidden className="size-4" />
                    </button>
                  </div>
                  <label className="block text-caption text-ink-muted">
                    Поисковые названия
                    <input
                      aria-label={`Поисковые названия объекта ${n}`}
                      aria-describedby={`object-aliases-hint${problem ? ` object-problem-${object.id}` : ""}`}
                      aria-invalid={Boolean(problem)}
                      className={`${control} mt-1 aria-invalid:border-warning`}
                      placeholder="Английские названия и варианты написания; можно оставить пустым"
                      value={aliasesText[object.id] ?? (object.aliases ?? []).join("; ")}
                      onChange={(event) => {
                        setAliasesText({ ...aliasesText, [object.id]: event.target.value });
                        change(object.id, { aliases: parseTerms(event.target.value) });
                      }}
                    />
                  </label>
                  <TermsDiff
                    criterion={{
                      terms: object.aliases ?? [],
                      suggested_terms: object.suggested_aliases ?? [],
                      origin: object.origin,
                    }}
                  />
                  {problem && (
                    <p id={`object-problem-${object.id}`} className="text-caption text-warning-ink">
                      {problem.message}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          <Button
            variant="link"
            className="mt-2"
            onClick={() =>
              update({
                objects: [
                  ...objects,
                  {
                    id: newId("obj"),
                    name: "",
                    scientific_name: "",
                    aliases: [],
                    suggested_aliases: [],
                    origin: "added",
                  },
                ],
              })
            }
          >
            <Plus aria-hidden className="size-4" />
            Добавить объект
          </Button>
        </>
      )}
    </Section>
  );
}

export function ParametersSection({ fields, update }: { fields: Fields; update: Update }) {
  const row = "grid grid-cols-[10rem_1fr] items-center gap-3";
  return (
    <Section number={2} title="Параметры" hint="Задайте основные параметры поиска.">
      <div className="grid grid-cols-2 gap-x-8 gap-y-2">
        <div className={row}>
          <span>Период публикации</span>
          <PeriodField value={fields.period} onChange={(period) => update({ period })} />
        </div>
        <div className={row}>
          <span>Вид публикации</span>
          <PublicationKindsField
            value={fields.publication_kinds}
            onChange={(publication_kinds) => update({ publication_kinds })}
          />
        </div>
        <div className={row}>
          <span>Тип публикации</span>
          <ArticleTypesField
            value={fields.article_types}
            onChange={(article_types) => update({ article_types })}
          />
        </div>
        <div className={row}>
          <span>Условия</span>
          <ConditionsField
            value={fields.conditions}
            onChange={(conditions) => update({ conditions })}
          />
        </div>
      </div>
    </Section>
  );
}

const parseTerms = (text: string) =>
  text
    .split(";")
    .map((term) => term.trim())
    .filter(Boolean);

/** Какие термины пользователь добавил к предложенным системой и какие убрал. */
function TermsDiff({
  criterion,
}: {
  criterion: Pick<Criterion, "terms" | "suggested_terms" | "origin">;
}) {
  if (criterion.origin === "added") return null;
  const added = criterion.terms.filter((t) => !criterion.suggested_terms.includes(t));
  const removed = criterion.suggested_terms.filter((t) => !criterion.terms.includes(t));
  if (!added.length && !removed.length) return null;
  return (
    <p className="mt-1 text-caption text-ink-muted">
      {added.length > 0 && <>Добавлено: {added.join(", ")}. </>}
      {removed.length > 0 && <>Убрано из предложенных: {removed.join(", ")}.</>}
    </p>
  );
}

export function SearchCriteriaSection({
  criteria,
  preparing,
  canReformulate,
  problems,
  update,
  onReformulate,
}: {
  criteria: Criterion[];
  preparing: boolean;
  canReformulate: boolean;
  problems: Schemas["Problem"][];
  update: Update;
  onReformulate: () => void;
}) {
  // Текст терминов хранится как ввели, чтобы «a; » не схлопывался при наборе.
  const [termsText, setTermsText] = useState<Record<string, string>>({});
  const [removed, setRemoved] = useState<Criterion[]>([]);
  const list = useCompactList(criteria, (c) => `${c.label} ${c.terms.join(" ")}`);

  const change = (id: string, patch: Partial<Criterion>) =>
    update({
      search_criteria: criteria.map((c) =>
        c.id === id ? { ...c, ...patch, origin: edited(c.origin) } : c,
      ),
    });

  function remove(criterion: Criterion) {
    update({ search_criteria: criteria.filter((c) => c.id !== criterion.id) });
    if (criterion.origin !== "added") setRemoved([...removed, criterion]);
  }

  const cell = "border-line px-2 py-1.5 align-top";

  return (
    <Section
      number={3}
      title="Критерии поискового запроса"
      hint="Внутри строки достаточно одного термина (OR); каждая дополнительная строка сужает поиск (AND)."
      action={
        <Button variant="outlineAccent" disabled={!canReformulate} onClick={onReformulate}>
          <RefreshCw aria-hidden className="size-4" />
          Переформулировать по вопросу
        </Button>
      }
    >
      {preparing ? (
        <Skeleton label="Критерии готовятся" />
      ) : (
        <>
          {list.compact && (
            <ListFilter
              label="Найти критерий"
              count={`Критериев: ${criteria.length}`}
              filter={list.filter}
              setFilter={list.setFilter}
            />
          )}
          <div
            className={`overflow-hidden rounded-control border border-line ${list.compact ? "max-h-[28rem] overflow-y-auto" : ""}`}
          >
            <table className="w-full border-collapse">
              <thead className="sticky top-0 bg-canvas text-left text-caption text-ink-muted">
                <tr>
                  <th className={`${cell} w-1/3 border-r font-normal`}>Критерий</th>
                  <th className={`${cell} font-normal`}>Поисковые термины</th>
                  <th className={`${cell} w-10`}>
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.visible.map((criterion) => {
                  const n = criteria.indexOf(criterion) + 1;
                  const name = criterion.label || `критерий ${n}`;
                  const problem = problems.find((p) => p.target === criterion.id);
                  const text = termsText[criterion.id] ?? criterion.terms.join("; ");
                  const termsProps = {
                    "aria-label": `Поисковые термины: ${name}`,
                    "aria-invalid": Boolean(problem),
                    "aria-describedby": problem ? `problem-${criterion.id}` : undefined,
                    className: `${control} aria-invalid:border-warning`,
                    value: text,
                    onChange: (event: { target: { value: string } }) => {
                      setTermsText({ ...termsText, [criterion.id]: event.target.value });
                      change(criterion.id, { terms: parseTerms(event.target.value) });
                    },
                  };
                  return (
                    <tr key={criterion.id} className="border-t border-line">
                      <td className={`${cell} border-r`}>
                        <input
                          aria-label={`Критерий ${n}`}
                          className={control}
                          value={criterion.label}
                          onChange={(event) => change(criterion.id, { label: event.target.value })}
                        />
                        <div className="mt-1 empty:hidden">
                          <OriginTag origin={criterion.origin} />
                        </div>
                      </td>
                      <td className={cell}>
                        {criterion.terms.length > COMPACT_TERMS ? (
                          <>
                            <textarea rows={4} {...termsProps} />
                            <p className="mt-1 text-caption text-ink-muted">
                              Терминов: {criterion.terms.length}
                            </p>
                          </>
                        ) : (
                          <input {...termsProps} />
                        )}
                        <TermsDiff criterion={criterion} />
                        {problem && (
                          <p
                            id={`problem-${criterion.id}`}
                            className="mt-1 flex items-center gap-1 text-warning-ink"
                          >
                            <CircleAlert aria-hidden className="size-4" />
                            {problem.message}
                          </p>
                        )}
                      </td>
                      <td className={cell}>
                        <button
                          type="button"
                          aria-label={`Удалить критерий ${n} «${name}»`}
                          className="p-1.5 text-ink-muted hover:text-ink"
                          onClick={() => remove(criterion)}
                        >
                          <Trash2 aria-hidden className="size-4" />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Button
            variant="link"
            className="mt-2"
            onClick={() =>
              update({
                search_criteria: [
                  ...criteria,
                  {
                    id: newId("crit"),
                    label: "",
                    terms: [],
                    suggested_terms: [],
                    origin: "added",
                  },
                ],
              })
            }
          >
            <Plus aria-hidden className="size-4" />
            Добавить критерий
          </Button>
          {removed.length > 0 && (
            <p className="mt-2 text-ink-muted">
              Удалено: {removed.map((c) => `«${c.label}»`).join(", ")}.{" "}
              <button
                type="button"
                className="text-accent hover:text-accent-strong"
                onClick={() => {
                  update({ search_criteria: [...criteria, ...removed] });
                  setRemoved([]);
                }}
              >
                Вернуть
              </button>
            </p>
          )}
        </>
      )}
    </Section>
  );
}

const RESOURCES: { value: Resource; label: string }[] = [
  { value: "pubmed", label: "PubMed" },
  { value: "europe_pmc", label: "Europe PMC" },
];

export function ResourcesSection({
  resources,
  problem,
  update,
}: {
  resources: Fields["resources"];
  problem?: Schemas["Problem"];
  update: Update;
}) {
  return (
    <Section number={4} title="Ресурсы" hint="Выберите базы данных для поиска.">
      <fieldset
        aria-labelledby="section-4"
        aria-describedby="resources-note"
        className="flex items-center gap-3"
      >
        {RESOURCES.map((resource) => (
          <label
            key={resource.value}
            className="flex items-center gap-2 rounded-control border border-line px-3 py-1.5"
          >
            <input
              type="checkbox"
              className="size-4 accent-accent"
              checked={resources.includes(resource.value)}
              onChange={(event) =>
                update({
                  resources: event.target.checked
                    ? [...resources, resource.value]
                    : resources.filter((r) => r !== resource.value),
                })
              }
            />
            {resource.label}
          </label>
        ))}
        <span
          id="resources-note"
          className={
            problem ? "flex items-center gap-1 text-warning-ink" : "text-caption text-ink-muted"
          }
        >
          {problem && <CircleAlert aria-hidden className="size-4" />}
          {problem ? problem.message : "Нужен хотя бы один ресурс."}
        </span>
      </fieldset>
    </Section>
  );
}
