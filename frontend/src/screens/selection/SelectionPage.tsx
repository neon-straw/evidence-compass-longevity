import * as Popover from "@radix-ui/react-popover";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ArrowUpDown, Filter, Plus, Search, SquareArrowOutUpRight } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { usePersistentState, useScrollRestore } from "../../ui/usePersistentState";
import { useNavigate } from "react-router";
import { api, isMockApi } from "../../api/client";
import { PROVENANCE_LABEL } from "../../api/labels";
import type { Decision, Schemas } from "../../api/types";
import { STUDY_KEY } from "../../app/useReachedStep";
import { Button } from "../../ui/Button";
import { LoadFailed } from "../../ui/LoadFailed";
import { BottomBar, PageHeader, Panel } from "../../ui/Page";
import { plural } from "../../ui/plural";
import { StatusPill } from "../../ui/StatusPill";
import { SELECTION_KEY, useSelectionActions } from "./actions";
import { DEFAULT_USER_REASON, DecisionControl } from "./DecisionControl";
import {
  DECISIONS,
  DECISION_HINT,
  EXTRACTION_HINT,
  OPEN_ACCESS,
  PILL_WIDTH,
  VERSION_TYPE,
  provenanceLabel,
} from "./labels";
import { Hint } from "../../ui/Hint";
import { ManualAddDialog } from "./ManualAddDialog";
import { PublicationCard } from "./PublicationCard";

type Row = Schemas["PublicationRow"];
type SortKey =
  "title" | "year" | "version_type" | "provenance" | "decision" | "versions" | "open_access";
type Filters = {
  versionType: Row["version_type"][];
  provenance: Row["provenance"][number][];
  openAccess: Row["open_access"][];
};

const NO_FILTERS: Filters = { versionType: [], provenance: [], openAccess: [] };
const DECISION_ORDER: Record<Decision, number> = { included: 0, needs_review: 1, excluded: 2 };

const SORT_VALUE: Record<SortKey, (row: Row) => string | number> = {
  title: (row) => row.title,
  year: (row) => row.year ?? 0,
  version_type: (row) => VERSION_TYPE[row.version_type],
  provenance: provenanceLabel,
  decision: (row) => DECISION_ORDER[row.decision],
  versions: (row) => row.versions,
  open_access: (row) => OPEN_ACCESS[row.open_access].label,
};

function compare(a: string | number, b: string | number): number {
  return typeof a === "string" && typeof b === "string" ? a.localeCompare(b, "ru") : +a - +b;
}

const works = (n: number) => plural(n, ["работа", "работы", "работ"]);

export function SelectionPage() {
  const selection = useQuery({
    queryKey: SELECTION_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/selection");
      if (!data) throw new Error("Отбор не загружен");
      return data;
    },
  });
  if (selection.isError) return <LoadFailed what="отбор" onRetry={() => selection.refetch()} />;
  if (!selection.data) return null;
  return <SelectionView selection={selection.data} />;
}

function SelectionView({ selection }: { selection: Schemas["Selection"] }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const actions = useSelectionActions();
  // Поиск, фильтры и сортировка сохраняются при уходе на другой шаг (shape §4.1).
  const [text, setText] = usePersistentState("selection:text", "");
  const [decisionFilter, setDecisionFilter] = usePersistentState<Decision | "all">(
    "selection:decision",
    "all",
  );
  // Ключи сменились вместе с колонкой «Открытый текст»: прежние значения вкладки не читаются.
  const [filters, setFilters] = usePersistentState<Filters>("selection:filters:v2", NO_FILTERS);
  const [sort, setSort] = usePersistentState<{ key: SortKey; dir: 1 | -1 } | null>(
    "selection:sort:v2",
    null,
  );
  const [openCard, setOpenCard] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const cardTrigger = useRef<HTMLElement | null>(null);
  const searchField = useRef<HTMLInputElement>(null);
  useScrollRestore("selection:scroll", true);

  const { publications, selected_count, limit } = selection;
  const visible = useMemo(() => {
    const q = text.trim().toLowerCase();
    const rows = publications.filter(
      (row) =>
        (!q || [row.title, row.authors, row.doi].some((f) => f?.toLowerCase().includes(q))) &&
        (decisionFilter === "all" || row.decision === decisionFilter) &&
        (!filters.versionType.length || filters.versionType.includes(row.version_type)) &&
        (!filters.provenance.length ||
          row.provenance.some((p) => filters.provenance.includes(p))) &&
        (!filters.openAccess.length || filters.openAccess.includes(row.open_access)),
    );
    if (!sort) return rows;
    const value = SORT_VALUE[sort.key];
    return [...rows].sort((a, b) => compare(value(a), value(b)) * sort.dir);
  }, [publications, text, decisionFilter, filters, sort]);
  const hidden = publications.length - visible.length;
  const sources = [...new Set(publications.flatMap((row) => row.provenance))]
    .filter((p) => p !== "manual")
    .map((p) => PROVENANCE_LABEL[p]);

  async function toggleSelected(row: Row) {
    setMessage(await actions.changeSelected(row.id, !row.selected));
  }

  async function clearSelection() {
    let failure: string | null = null;
    for (const row of publications.filter((r) => r.selected)) {
      failure ??= await actions.changeSelected(row.id, false);
    }
    setMessage(failure);
  }

  async function proceed() {
    setMessage(null);
    try {
      const { data, error } = await api.POST("/api/selection/proceed");
      if (!data) {
        setMessage(error && "message" in error ? error.message : "Переход не выполнен.");
        return;
      }
      await queryClient.invalidateQueries({ queryKey: STUDY_KEY });
      navigate("/preparation");
    } catch {
      setMessage("Переход не выполнен: сервер недоступен.");
    }
  }

  function header(key: SortKey, label: string, hint?: string) {
    const active = sort?.key === key;
    const button = (
      <button
        type="button"
        className="inline-flex items-center gap-1 hover:text-ink"
        onClick={() => setSort({ key, dir: active && sort!.dir === 1 ? -1 : 1 })}
      >
        {label}
        <ArrowUpDown aria-hidden className={`size-3.5 ${active ? "text-accent" : ""}`} />
      </button>
    );
    return (
      <th
        className="px-3 py-2.5 font-normal"
        aria-sort={active ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}
      >
        {hint ? <Hint text={hint}>{button}</Hint> : button}
      </th>
    );
  }

  // Номер строки — в порядке, который видит пользователь.
  const cardRow =
    visible.findIndex((row) => row.id === openCard) + 1 ||
    publications.findIndex((row) => row.id === openCard) + 1;

  return (
    <>
      <Panel className="px-6 pt-5 pb-2">
        <PageHeader
          title="Отбор публикаций"
          subtitle={`${isMockApi ? "Демо: " : ""}${plural(publications.length, ["запись", "записи", "записей"])}${sources.length ? ` из ${sources.join(" и ")}` : ""}`}
        />

        <div className="mt-4 flex items-center gap-3 border-t border-line pt-4">
          <label className="relative max-w-xl flex-1">
            <Search
              aria-hidden
              className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-muted"
            />
            <input
              ref={searchField}
              aria-label="Поиск по названию, автору, DOI"
              placeholder="Поиск по названию, автору, DOI…"
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="w-full rounded-control border border-line py-2 pr-3 pl-9 outline-none focus:border-accent"
            />
          </label>
          <select
            aria-label="Решение"
            value={decisionFilter}
            onChange={(event) => setDecisionFilter(event.target.value as Decision | "all")}
            className="rounded-control border border-line bg-surface px-3 py-2 outline-none focus:border-accent"
          >
            <option value="all">Все решения</option>
            {(Object.keys(DECISIONS) as Decision[]).map((d) => (
              <option key={d} value={d}>
                {DECISIONS[d].long}
              </option>
            ))}
          </select>
          <FiltersPopover filters={filters} onChange={setFilters} />
          {hidden > 0 && <span className="text-ink-muted">Скрыто фильтрами: {hidden}</span>}
          <span className="ml-auto flex items-center gap-2 text-accent">
            <span>
              Выбрано {selected_count} из {limit}
            </span>
            {selected_count > 0 && (
              <>
                <span aria-hidden>·</span>
                <Button variant="link" onClick={clearSelection}>
                  Снять все
                </Button>
              </>
            )}
          </span>
          <Button onClick={() => setAdding(true)}>
            <Plus aria-hidden className="size-4" />
            Добавить вручную
          </Button>
        </div>

        {message && (
          <p role="alert" className="mt-3 rounded-control bg-warning-soft px-4 py-2">
            {message}
          </p>
        )}

        {publications.length === 0 ? (
          <div role="status" className="py-10 text-center">
            <p className="text-lg font-medium">Поиск не нашёл записей</p>
            <p className="mt-1 text-ink-muted">
              Добавьте известную работу вручную или измените критерии и повторите поиск.
            </p>
            <div className="mt-4 flex justify-center gap-3">
              <Button onClick={() => navigate("/criteria")}>Изменить критерии</Button>
              <Button variant="primary" onClick={() => setAdding(true)}>
                Добавить вручную
              </Button>
            </div>
          </div>
        ) : (
          <table className="mt-3 w-full border-collapse text-left">
            <thead className="border-y border-line text-ink-muted">
              <tr>
                <th className="w-px px-3 py-2.5 text-center font-normal">
                  <Hint text={EXTRACTION_HINT}>
                    <span tabIndex={0}>Извлечь</span>
                  </Hint>
                </th>
                {header("title", "Публикация")}
                {header("year", "Год")}
                {header("version_type", "Тип")}
                {header("provenance", "Источник")}
                {header("decision", "Соответствует", DECISION_HINT)}
                {header("versions", "Версии")}
                {header("open_access", "Открытый текст")}
                <th className="w-12">
                  <span className="sr-only">Карточка</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr
                  key={row.id}
                  className={`border-b border-line ${row.selected ? "bg-accent-soft/70 shadow-[inset_3px_0_0_var(--color-accent)]" : ""}`}
                >
                  <td className="px-3 py-2 text-center">
                    {row.select_blocked_reason ? (
                      // Недоступный чекбокс не получает событий: причину показывает обёртка.
                      <Hint text={row.select_blocked_reason}>
                        <span tabIndex={0} className="inline-flex">
                          <input
                            type="checkbox"
                            aria-label={`Выбрать «${row.title}»`}
                            aria-describedby={`blocked-${row.id}`}
                            className="size-4 accent-accent"
                            checked={row.selected}
                            disabled
                          />
                          <span id={`blocked-${row.id}`} hidden>
                            {row.select_blocked_reason}
                          </span>
                        </span>
                      </Hint>
                    ) : (
                      <input
                        type="checkbox"
                        aria-label={`Выбрать «${row.title}»`}
                        className="size-4 accent-accent"
                        checked={row.selected}
                        onChange={() => toggleSelected(row)}
                      />
                    )}
                  </td>
                  <td className="max-w-sm px-3 py-2 text-link">{row.title}</td>
                  <td className="px-3 py-2">{row.year ?? "—"}</td>
                  <td className="px-3 py-2 text-link">{VERSION_TYPE[row.version_type]}</td>
                  <td className="px-3 py-2">
                    {row.provenance.includes("manual") ? (
                      <span className="rounded-full bg-accent-soft px-2 py-0.5 text-caption text-accent">
                        Добавлено вручную
                      </span>
                    ) : (
                      <span className="text-link">{provenanceLabel(row)}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <DecisionControl
                      title={row.title}
                      decision={row.decision}
                      onChange={(decision) =>
                        void actions
                          .changeDecision(row.id, decision, DEFAULT_USER_REASON)
                          .then(setMessage)
                      }
                    />
                  </td>
                  <td className="px-3 py-2">
                    <span className="text-link">
                      {plural(row.versions, ["версия", "версии", "версий"])}
                    </span>
                    {row.version_link === "needs_review" && (
                      <span className="block text-caption text-warning-ink">
                        связь не подтверждена
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <StatusPill tone={OPEN_ACCESS[row.open_access].tone} className={PILL_WIDTH}>
                      <span className="flex-1 text-center">
                        {OPEN_ACCESS[row.open_access].label}
                      </span>
                      {/* Пустое место под размер иконки: надпись по центру плашки. */}
                      <span aria-hidden className="size-4" />
                    </StatusPill>
                  </td>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      aria-label={`Открыть карточку публикации «${row.title}»`}
                      className="p-1.5 text-ink-muted hover:text-accent"
                      onClick={(event) => {
                        cardTrigger.current = event.currentTarget;
                        setOpenCard(row.id);
                      }}
                    >
                      <SquareArrowOutUpRight aria-hidden className="size-5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <BottomBar
        status={
          <span id="proceed-status">
            Выбрано {selected_count} из {limit} публикаций
            {selection.proceed_blocked_reason && (
              <span className="ml-3 text-warning-ink">{selection.proceed_blocked_reason}</span>
            )}
          </span>
        }
      >
        <Button onClick={() => navigate("/search")}>Вернуться к поиску</Button>
        <Button
          variant="primary"
          disabled={selection.proceed_blocked_reason !== null}
          aria-describedby="proceed-status"
          onClick={proceed}
        >
          Перейти к документам · {works(selected_count)}
          <ArrowRight aria-hidden className="size-4" />
        </Button>
      </BottomBar>

      <PublicationCard
        publicationId={openCard}
        rowNumber={cardRow}
        returnFocus={cardTrigger}
        fallbackFocus={searchField}
        onClose={() => setOpenCard(null)}
      />
      <ManualAddDialog open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

function FiltersPopover({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  const active = filters.versionType.length + filters.provenance.length + filters.openAccess.length;
  const groups = [
    {
      key: "versionType",
      title: "Тип",
      options: [
        ["journal", "Журнальная версия"],
        ["preprint", "Препринт"],
      ],
    },
    {
      key: "provenance",
      title: "Источник",
      options: [
        ["pubmed", "PubMed"],
        ["europe_pmc", "Europe PMC"],
        ["manual", "Добавлено вручную"],
      ],
    },
    {
      key: "openAccess",
      title: "Открытый текст",
      options: [
        ["open", "Есть"],
        ["closed", "Нет"],
        ["unknown", "Нет данных"],
      ],
    },
  ] as const;

  return (
    <Popover.Root>
      <Popover.Trigger className="inline-flex items-center gap-2 rounded-control border border-line px-3 py-2 hover:border-ink-muted">
        <Filter aria-hidden className="size-4" />
        Фильтры{active > 0 && ` · ${active}`}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          className="z-30 w-64 space-y-3 rounded-control border border-line bg-surface p-4 shadow-md"
        >
          {groups.map((group) => {
            const values = filters[group.key] as string[];
            return (
              <fieldset key={group.key}>
                <legend className="font-medium">{group.title}</legend>
                {group.options.map(([value, label]) => (
                  <label key={value} className="mt-1 flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={values.includes(value)}
                      onChange={(event) =>
                        onChange({
                          ...filters,
                          [group.key]: event.target.checked
                            ? [...values, value]
                            : values.filter((v) => v !== value),
                        })
                      }
                    />
                    {label}
                  </label>
                ))}
              </fieldset>
            );
          })}
          {active > 0 && (
            <Button variant="link" onClick={() => onChange(NO_FILTERS)}>
              Сбросить фильтры
            </Button>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
