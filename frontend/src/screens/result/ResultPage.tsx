import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Popover from "@radix-ui/react-popover";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpDown,
  ArrowRight,
  BarChart3,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Columns3,
  FileText,
  Filter,
  MessageCircleQuestion,
  MoreHorizontal,
  Quote,
  RefreshCw,
  Search,
  UserCheck,
} from "lucide-react";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { api, isMockApi } from "../../api/client";
import { PROVENANCE_LABEL } from "../../api/labels";
import { failureReason } from "../../api/errors";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { Hint } from "../../ui/Hint";
import { LoadFailed } from "../../ui/LoadFailed";
import { Panel } from "../../ui/Page";
import { plural } from "../../ui/plural";
import { StatusPill } from "../../ui/StatusPill";
import { usePersistentState } from "../../ui/usePersistentState";
import { PublicationCard } from "../selection/PublicationCard";
import { CorrectionDialog } from "./CorrectionDialog";
import { EvidencePanel } from "./EvidencePanel";
import { AbsentWorks, SummaryFindings } from "./SummaryFindings";
import {
  KIND,
  ORIGIN,
  type Correction,
  RESULT_KEY,
  REVIEW,
  SETTING,
  VERIFICATION,
  VERIFICATION_FILTER,
  type Verification,
  type VerificationFilter,
  evidenceKey,
  isMatched,
  matchesVerification,
  resultQuery,
  rowLabel,
  precisionAndSpreadText,
  valueText,
  verification,
} from "./format";

type Result = Schemas["Result"];
type Row = Schemas["MeasureRow"];
type Column = "uncertainty" | "conditions" | "publication" | "review";
type SortKey = "number" | "subject" | "kind" | "value" | "publication" | "review";

const OPTIONAL_COLUMNS: Record<Column, string> = {
  uncertainty: "Точность и разброс",
  conditions: "Условия",
  publication: "Публикация",
  review: "Проверка",
};
const SORT_VALUE: Record<SortKey, (r: Row) => string | number> = {
  number: (r) => r.number,
  subject: (r) => r.subject.label,
  kind: (r) => KIND[r.measure_kind],
  value: (r) => Number(r.value_original) || 0,
  publication: (r) => r.publication_label,
  review: (r) => REVIEW[r.review_status].label,
};

export function ResultPage() {
  const result = useQuery(resultQuery);
  if (result.isError) return <LoadFailed what="результат" onRetry={() => result.refetch()} />;
  if (!result.data) return null;
  return <ResultView result={result.data} />;
}

function Block({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <Panel className="flex items-start gap-5 px-5 py-4">
      <span className="grid size-11 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
        {icon}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </Panel>
  );
}

function ResultView({ result }: { result: Result }) {
  const queryClient = useQueryClient();
  // Выбранная строка не переживает перезагрузку: панель не должна открываться сама.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [correcting, setCorrecting] = useState<Row | null>(null);
  const [cardId, setCardId] = useState<string | null>(null);
  const [cardMeasureId, setCardMeasureId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const rowButtons = useRef(new Map<string, HTMLButtonElement>());
  const cardTrigger = useRef<HTMLElement | null>(null);
  const searchField = useRef<HTMLInputElement>(null);
  const evidenceHeading = useRef<HTMLHeadingElement>(null);

  const { revision, summary, coverage, measures } = result;
  const selected = measures.find((r) => r.id === selectedId) ?? null;
  const stale = revision.freshness === "stale";

  /** Выбирает строку, прокручивает её над панелью основания и переводит фокус в панель. */
  function select(id: string) {
    if (selectedId === id) {
      closeEvidence();
      return;
    }
    setSelectedId(id);
    requestAnimationFrame(() => {
      rowButtons.current.get(id)?.scrollIntoView({ block: "center" });
      evidenceHeading.current?.focus({ preventScroll: true });
    });
  }

  async function refresh() {
    setMessage(null);
    try {
      const { data, error } = await api.POST("/api/result/refresh");
      if (data) queryClient.setQueryData(RESULT_KEY, data);
      else setMessage(`Краткий вывод не обновлён: ${failureReason(error)}.`);
    } catch {
      setMessage("Краткий вывод не обновлён: сервер недоступен.");
    }
  }

  async function review(row: Row) {
    try {
      const { data, error } = await api.POST("/api/measures/{measure_id}/review", {
        params: { path: { measure_id: row.id } },
        body: { review_status: "checked" },
      });
      setMessage(data ? null : `Проверка не сохранена: ${failureReason(error)}.`);
    } catch {
      setMessage("Проверка не сохранена: сервер недоступен.");
    }
    await queryClient.invalidateQueries({ queryKey: RESULT_KEY });
  }

  async function correct(row: Row, change: Correction) {
    try {
      const { data, error } = await api.POST("/api/measures/{measure_id}/correction", {
        params: { path: { measure_id: row.id } },
        body: {
          expected_revision: revision.number,
          value_original: change.value,
          unit_original: change.unit,
          comment: change.comment,
        },
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: RESULT_KEY }),
        queryClient.invalidateQueries({ queryKey: evidenceKey(row.id) }),
      ]);
      return data ? null : `Исправление не сохранено: ${failureReason(error)}.`;
    } catch {
      return "Исправление не сохранено: сервер недоступен.";
    }
  }

  function closeEvidence() {
    const id = selectedId;
    setSelectedId(null);
    // Строка могла скрыться фильтром — тогда фокус в поиск по таблице.
    const target = id ? rowButtons.current.get(id) : null;
    (target?.isConnected ? target : searchField.current)?.focus();
  }

  return (
    <div className={`flex flex-col gap-3 ${selected ? "pb-[42vh]" : ""}`}>
      {stale && (
        <div
          role="status"
          className="flex items-center gap-4 rounded-panel border border-warning/40 bg-warning-soft px-5 py-3"
        >
          <p className="flex-1">
            <span className="font-medium">Краткий вывод устарел.</span> {revision.stale_reason}{" "}
            Таблица — ревизия {revision.number}; краткий вывод построен по ревизии{" "}
            {revision.summary_revision} и не учитывает правку.
          </p>
          <Button variant="primary" onClick={refresh}>
            <RefreshCw aria-hidden className="size-4" />
            Обновить краткий вывод
          </Button>
        </div>
      )}
      {message && (
        <p role="alert" className="rounded-control bg-warning-soft px-4 py-2">
          {message}
        </p>
      )}

      <Block icon={<MessageCircleQuestion aria-hidden className="size-5" />}>
        <h1 className="text-ink-muted">Исследовательский вопрос</h1>
        <p className="mt-1 max-w-4xl text-xl">{result.question}</p>
      </Block>

      <SearchBlock search={result.search} />

      <Block icon={<FileText aria-hidden className="size-5" />}>
        <div className="flex items-center gap-3">
          <h2 className="text-ink-muted">Краткий вывод</h2>
          {summary.findings.length > 0 && (
            <span className="rounded-full bg-warning-soft px-2.5 py-0.5 text-caption text-warning-ink">
              {stale ? "Устарел" : "Предварительно"}
            </span>
          )}
          {!revision.complete && (
            <span className="rounded-full bg-neutral-soft px-2.5 py-0.5 text-caption text-ink-muted">
              Неполный результат
            </span>
          )}
        </div>
        {summary.findings.length > 0 ? (
          <SummaryFindings findings={summary.findings} onSelect={select} />
        ) : (
          <p className="mt-1 text-lg">{summary.absent_reason}</p>
        )}
        <div className="mt-5 border-t border-line pt-1" />
        {summary.findings.length === 0 && <h3 className="mt-3 font-medium">Почему нет вывода</h3>}
        <AbsentWorks
          works={summary.works ?? []}
          onOpenPublication={(id, trigger) => {
            cardTrigger.current = trigger;
            setCardId(id);
            setCardMeasureId(null);
          }}
        />
        {!!summary.excluded_reasons?.length && (
          <details className="mt-5 text-caption">
            <summary className="cursor-pointer font-medium hover:underline">
              Не включены в вывод:{" "}
              {plural(summary.excluded_count ?? 0, ["строка", "строки", "строк"])}
            </summary>
            <ul className="mt-2 max-w-4xl space-y-2 text-ink-muted">
              {summary.excluded_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </details>
        )}
        {summary.limits.length > 0 && (
          <div className="mt-5 text-caption">
            <h4 className="font-medium">Ограничения вывода</h4>
            <ul className="mt-2 max-w-4xl space-y-1 text-ink-muted">
              {summary.limits.map((limit) => (
                <li key={limit}>{limit}</li>
              ))}
            </ul>
          </div>
        )}
        <p className="mt-2 text-caption text-ink-muted">
          Система извлекает сведения из публикаций. Самостоятельная оценка качества исследований в
          этот этап не входит.
        </p>
      </Block>

      <CoverageBlock coverage={coverage} />

      <MeasuresTable
        rows={measures}
        selectedId={selectedId}
        rowButtons={rowButtons}
        searchField={searchField}
        onSelect={select}
        onReview={review}
        onCorrect={setCorrecting}
        onOpenCard={(row, trigger) => {
          cardTrigger.current = trigger;
          setCardId(row.publication_id);
          setCardMeasureId(row.id);
        }}
      />

      {selected && (
        <EvidencePanel
          row={selected}
          headingRef={evidenceHeading}
          onCorrect={() => setCorrecting(selected)}
          onClose={closeEvidence}
          onDismiss={() => setSelectedId(null)}
          suspended={correcting !== null || cardId !== null}
        />
      )}
      <CorrectionDialog
        row={correcting}
        onClose={() => setCorrecting(null)}
        onSave={(change) => correct(correcting!, change)}
      />
      <PublicationCard
        publicationId={cardId}
        evidenceMeasureId={cardMeasureId}
        rowNumber={null}
        readOnly
        returnFocus={cardTrigger}
        fallbackFocus={searchField}
        onClose={() => setCardId(null)}
      />
    </div>
  );
}

function SearchBlock({ search }: { search: Result["search"] }) {
  const [open, setOpen] = useState(false);
  const period =
    search.period.from_year || search.period.to_year
      ? `${search.period.from_year ?? "…"} – ${search.period.to_year ?? "…"}`
      : "без ограничения";
  const fields: [string, string][] = [
    ["Период", period],
    ["Вид публикации", search.publication_kinds.join(" и ") || "без ограничения"],
    ["Условия", search.conditions.join(" и ") || "без ограничения"],
    ["Ресурсы", search.resources.map((r) => PROVENANCE_LABEL[r]).join(" · ")],
  ];
  return (
    <Block icon={<Search aria-hidden className="size-5" />}>
      <div className="flex items-center gap-6">
        <h2 className="w-40 shrink-0 font-semibold">Параметры поиска</h2>
        <dl className="flex flex-1 divide-x divide-line">
          {fields.map(([label, value]) => (
            <div key={label} className="px-6 first:pl-0">
              <dt className="text-caption text-ink-muted">{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <Button variant="link" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Скрыть полный запрос" : "Показать полный запрос"}
          <ChevronDown aria-hidden className={`size-4 transition ${open ? "rotate-180" : ""}`} />
        </Button>
      </div>
      {open && (
        <div className="mt-3 space-y-2">
          {search.queries.map((q) => (
            <div key={q.resource}>
              <p className="text-caption text-ink-muted">{PROVENANCE_LABEL[q.resource]}</p>
              <pre className="overflow-x-auto rounded-control bg-canvas p-3 text-caption break-words whitespace-pre-wrap">
                {q.query}
              </pre>
            </div>
          ))}
        </div>
      )}
    </Block>
  );
}

function CoverageBlock({ coverage }: { coverage: Result["coverage"] }) {
  const details: [string, number][] = [
    ["Полных текстов", coverage.full_texts],
    ["Недоступно", coverage.unavailable],
    ["Не обработано", coverage.not_processed],
  ];
  return (
    <Block icon={<BarChart3 aria-hidden className="size-5" />}>
      <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <h2 className="w-40 shrink-0 pt-1 font-semibold">Охват</h2>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-lg">
            <span>
              <strong className="font-semibold tabular-nums">{coverage.found}</strong> найдено
            </span>
            <ArrowRight aria-hidden className="size-4 text-ink-muted" />
            <span>
              <strong className="font-semibold tabular-nums">{coverage.selected}</strong> отобрано
            </span>
          </p>
          <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-caption text-ink-muted">
            {details.map(([label, count]) => (
              <div key={label} className="flex gap-1">
                <dt>{label}:</dt>
                <dd className="tabular-nums">{count}</dd>
              </div>
            ))}
          </dl>
        </div>
        <Link
          to="/result/coverage"
          className="flex items-center gap-1 pt-1 text-accent hover:text-accent-strong"
        >
          Подробнее
          <ChevronRight aria-hidden className="size-4" />
        </Link>
      </div>
    </Block>
  );
}

function MeasuresTable({
  rows,
  selectedId,
  rowButtons,
  searchField,
  onSelect,
  onReview,
  onCorrect,
  onOpenCard,
}: {
  rows: Row[];
  selectedId: string | null;
  rowButtons: React.RefObject<Map<string, HTMLButtonElement>>;
  searchField: React.RefObject<HTMLInputElement | null>;
  onSelect: (id: string) => void;
  onReview: (row: Row) => void;
  onCorrect: (row: Row) => void;
  onOpenCard: (row: Row, trigger: HTMLElement) => void;
}) {
  const [text, setText] = usePersistentState("result:text", "");
  const [kind, setKind] = usePersistentState<Row["measure_kind"] | "all">("result:kind", "all");
  const [setting, setSetting] = usePersistentState<Row["conditions"]["setting"] | "all">(
    "result:setting",
    "all",
  );
  const [review, setReview] = usePersistentState<Row["review_status"] | "all">(
    "result:review",
    "all",
  );
  const [verificationFilter, setVerificationFilter] = usePersistentState<VerificationFilter>(
    "result:verification",
    "all",
  );
  const [hidden, setHidden] = usePersistentState<Column[]>("result:hidden", []);
  const [sort, setSort] = usePersistentState<{ key: SortKey; dir: 1 | -1 } | null>(
    "result:sort",
    null,
  );
  const show = (column: Column) => !hidden.includes(column);

  const visible = useMemo(() => {
    const q = text.trim().toLowerCase();
    const filtered = rows.filter(
      (r) =>
        (!q ||
          [r.subject.label, r.species_name_in_source, r.definition, r.publication_label]
            .join(" ")
            .toLowerCase()
            .includes(q)) &&
        (kind === "all" || r.measure_kind === kind) &&
        (setting === "all" || r.conditions.setting === setting) &&
        (review === "all" || r.review_status === review) &&
        matchesVerification(r, verificationFilter),
    );
    if (!sort) return filtered;
    const value = SORT_VALUE[sort.key];
    return [...filtered].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const order =
        typeof x === "string" && typeof y === "string" ? x.localeCompare(y, "ru") : +x - +y;
      return order * sort.dir;
    });
  }, [rows, text, kind, setting, review, verificationFilter, sort]);
  const kinds = [...new Set(rows.map((r) => r.measure_kind))];
  const activeFilters = [setting, review, verificationFilter].filter((f) => f !== "all").length;

  function header(key: SortKey | null, label: string, hint?: string) {
    const current = key && sort?.key === key;
    return (
      <th
        className="px-3 py-2.5 font-normal"
        aria-sort={
          key ? (current ? (sort!.dir === 1 ? "ascending" : "descending") : "none") : undefined
        }
      >
        {key ? (
          <button
            type="button"
            className="inline-flex items-center gap-1 hover:text-ink"
            onClick={() => setSort({ key, dir: current && sort!.dir === 1 ? -1 : 1 })}
          >
            {label}
            <ArrowUpDown aria-hidden className={`size-3.5 ${current ? "text-accent" : ""}`} />
          </button>
        ) : hint ? (
          <Hint text={hint}>
            <button
              type="button"
              className="cursor-help underline decoration-dotted underline-offset-4"
            >
              {label}
            </button>
          </Hint>
        ) : (
          label
        )}
      </th>
    );
  }

  return (
    <Panel className="pt-4">
      <h2 className="px-5 text-lg font-semibold">Численные показатели</h2>
      <div className="flex items-center gap-3 px-5 py-3">
        <label className="relative flex-1">
          <Search
            aria-hidden
            className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-muted"
          />
          <input
            ref={searchField}
            aria-label="Поиск по объектам, показателям, публикациям"
            placeholder="Поиск по объектам, показателям, публикациям…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="w-full rounded-control border border-line py-2 pr-3 pl-9 outline-none focus:border-accent"
          />
        </label>
        <select
          aria-label="Вид показателя"
          value={kind}
          onChange={(e) => setKind(e.target.value as Row["measure_kind"] | "all")}
          className="rounded-control border border-line bg-surface px-3 py-2 outline-none focus:border-accent"
        >
          <option value="all">Все показатели</option>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {KIND[k]}
            </option>
          ))}
        </select>
        <Popover.Root>
          <Popover.Trigger className="inline-flex items-center gap-2 rounded-control border border-line px-3 py-2 hover:border-ink-muted">
            <Filter aria-hidden className="size-4" />
            Фильтры{activeFilters > 0 && ` · ${activeFilters}`}
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content
              align="end"
              sideOffset={4}
              className="z-30 w-64 space-y-3 rounded-control border border-line bg-surface p-4 shadow-md"
            >
              <label className="block">
                <span className="text-caption text-ink-muted">Условия</span>
                <select
                  value={setting}
                  onChange={(e) =>
                    setSetting(e.target.value as Row["conditions"]["setting"] | "all")
                  }
                  className="mt-1 w-full rounded-control border border-line px-2 py-1.5"
                >
                  <option value="all">Все условия</option>
                  {(Object.keys(SETTING) as Row["conditions"]["setting"][]).map((s) => (
                    <option key={s} value={s}>
                      {SETTING[s]}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-caption text-ink-muted">Проверка извлечения</span>
                <select
                  value={review}
                  onChange={(e) => setReview(e.target.value as Row["review_status"] | "all")}
                  className="mt-1 w-full rounded-control border border-line px-2 py-1.5"
                >
                  <option value="all">Любая</option>
                  {(Object.keys(REVIEW) as Row["review_status"][]).map((s) => (
                    <option key={s} value={s}>
                      {REVIEW[s].label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-caption text-ink-muted">Сверка значения с документом</span>
                <select
                  value={verificationFilter}
                  onChange={(e) => setVerificationFilter(e.target.value as VerificationFilter)}
                  className="mt-1 w-full rounded-control border border-line px-2 py-1.5"
                >
                  {(Object.keys(VERIFICATION_FILTER) as VerificationFilter[]).map((f) => (
                    <option key={f} value={f}>
                      {VERIFICATION_FILTER[f]}
                    </option>
                  ))}
                </select>
              </label>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
        <Popover.Root>
          <Popover.Trigger className="inline-flex items-center gap-2 rounded-control border border-line px-3 py-2 hover:border-ink-muted">
            <Columns3 aria-hidden className="size-4" />
            Колонки
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content
              align="end"
              sideOffset={4}
              className="z-30 w-56 rounded-control border border-line bg-surface p-4 shadow-md"
            >
              <fieldset className="space-y-2">
                <legend className="font-medium">Показывать колонки</legend>
                {(Object.keys(OPTIONAL_COLUMNS) as Column[]).map((c) => (
                  <label key={c} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-accent"
                      checked={show(c)}
                      onChange={(e) =>
                        setHidden(e.target.checked ? hidden.filter((h) => h !== c) : [...hidden, c])
                      }
                    />
                    {OPTIONAL_COLUMNS[c]}
                  </label>
                ))}
              </fieldset>
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
        <span className="text-ink-muted">
          {plural(visible.length, ["строка", "строки", "строк"])}
          {visible.length < rows.length && ` из ${rows.length}`}
        </span>
      </div>

      {rows.length === 0 ? (
        <p role="status" className="border-t border-line px-5 py-8 text-center text-ink-muted">
          Допустимых строк нет. Причины по каждой работе — в охвате.
        </p>
      ) : (
        <table aria-label="Численные показатели" className="w-full border-collapse text-left">
          <thead className="border-y border-line text-ink-muted">
            <tr>
              {header("number", "ID")}
              {header("subject", "Объект")}
              {header("kind", "Показатель и определение")}
              {header("value", "Значение")}
              {show("uncertainty") && header(null, "Точность и разброс")}
              {show("conditions") &&
                header(
                  null,
                  "Условия",
                  "Условия содержания и наблюдения животных, к которым относится показатель: например, в природе или в неволе. Сведения извлечены из публикации.",
                )}
              {show("publication") && header("publication", "Публикация")}
              {show("review") && header("review", "Проверка")}
              <th className="w-12">
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const active = row.id === selectedId;
              const check = verification(row);
              return (
                <tr
                  key={row.id}
                  data-evidence-trigger
                  onClick={(event) => {
                    if (!(event.target instanceof Element)) return;
                    if (
                      event.target.closest("button, a, input, select, textarea, [role='menuitem']")
                    )
                      return;
                    onSelect(row.id);
                  }}
                  className={`cursor-pointer border-b border-line align-top hover:bg-accent-soft/40 ${active ? "bg-accent-soft/70 shadow-[inset_3px_0_0_var(--color-accent)]" : ""}`}
                >
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      ref={(el) => {
                        if (el) rowButtons.current.set(row.id, el);
                        else rowButtons.current.delete(row.id);
                      }}
                      aria-label={`Открыть основание строки ${rowLabel(row.number)}`}
                      data-evidence-trigger
                      aria-pressed={active}
                      onClick={() => onSelect(row.id)}
                      className="font-mono text-link hover:underline"
                    >
                      {rowLabel(row.number)}
                    </button>
                  </td>
                  <td className="max-w-56 px-3 py-2">
                    <p>{row.subject.label}</p>
                    <p className="text-caption text-ink-muted italic">
                      {row.species_name_in_source}
                    </p>
                  </td>
                  <td className="max-w-72 px-3 py-2">
                    <p>{KIND[row.measure_kind]}</p>
                    <p className="text-caption text-ink-muted">{row.definition}</p>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={isMatched(check) ? "" : "text-ink-muted"}>
                      {isMockApi ? "Демо · " : ""}
                      {valueText(row)}
                    </span>
                    {row.qualifier && (
                      <p className="max-w-48 text-caption whitespace-normal text-ink-muted">
                        * {row.qualifier}
                      </p>
                    )}
                    {check !== "verified" && <VerificationMark check={check} />}
                    {row.origin_kind === "secondary_report" && (
                      <p className="mt-1 flex items-center gap-1 text-caption text-ink-muted">
                        <Quote aria-hidden className="size-3.5 shrink-0" />
                        {ORIGIN.secondary_report.label}
                      </p>
                    )}
                  </td>
                  {show("uncertainty") && (
                    <td className="max-w-40 px-3 py-2">{precisionAndSpreadText(row)}</td>
                  )}
                  {show("conditions") && (
                    <td className="max-w-56 px-3 py-2">
                      <p>{SETTING[row.conditions.setting]}</p>
                      <p className="text-caption text-ink-muted">{row.conditions.text}</p>
                    </td>
                  )}
                  {show("publication") && (
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        className="text-link hover:underline"
                        aria-label={`Карточка публикации ${row.publication_label}`}
                        onClick={(e) => onOpenCard(row, e.currentTarget)}
                      >
                        {row.publication_label}
                      </button>
                    </td>
                  )}
                  {show("review") && (
                    <td className="px-3 py-2">
                      <StatusPill tone={REVIEW[row.review_status].tone}>
                        {REVIEW[row.review_status].label}
                      </StatusPill>
                    </td>
                  )}
                  <td className="px-3 py-2">
                    <RowMenu
                      row={row}
                      onSelect={() => onSelect(row.id)}
                      onReview={() => onReview(row)}
                      onCorrect={() => onCorrect(row)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

/** Сверка значения с документом не прошла автоматически — видно без панели основания. */
function VerificationMark({ check }: { check: Exclude<Verification, "verified"> }) {
  const { tone, label } = VERIFICATION[check];
  const Icon = tone === "warning" ? CircleAlert : UserCheck;
  return (
    <p
      className={`mt-1 flex items-center gap-1 text-caption ${tone === "warning" ? "text-warning-ink" : "text-ink-muted"}`}
    >
      <Icon aria-hidden className="size-3.5 shrink-0" />
      {label}
    </p>
  );
}

function RowMenu({
  row,
  onSelect,
  onReview,
  onCorrect,
}: {
  row: Row;
  onSelect: () => void;
  onReview: () => void;
  onCorrect: () => void;
}) {
  const item =
    "cursor-pointer rounded-control px-2 py-1.5 outline-none data-disabled:cursor-not-allowed data-disabled:text-ink-muted data-highlighted:bg-accent-soft";
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        aria-label={`Действия со строкой ${rowLabel(row.number)}`}
        className="rounded-control p-1 text-ink-muted hover:text-ink"
      >
        <MoreHorizontal aria-hidden className="size-5" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={4}
          className="z-30 min-w-56 rounded-control border border-line bg-surface p-1 shadow-md"
        >
          <DropdownMenu.Item className={item} onSelect={onSelect}>
            Открыть основание
          </DropdownMenu.Item>
          <DropdownMenu.Item
            className={item}
            disabled={row.review_status === "checked"}
            onSelect={onReview}
          >
            Отметить: извлечение проверено
          </DropdownMenu.Item>
          <DropdownMenu.Item className={item} onSelect={onCorrect}>
            Исправить извлечение
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
