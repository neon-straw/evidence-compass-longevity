import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUpDown,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  File,
  FilePlus,
  Info,
  ListFilter,
  RefreshCw,
  Search,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, isMockApi } from "../../api/client";
import { failureReason } from "../../api/errors";
import type { Schemas } from "../../api/types";
import { STUDY_KEY } from "../../app/useReachedStep";
import { Button } from "../../ui/Button";
import { LoadFailed } from "../../ui/LoadFailed";
import { Modal } from "../../ui/Modal";
import { BottomBar, PageHeader, Panel } from "../../ui/Page";
import { plural } from "../../ui/plural";
import { StatusPill } from "../../ui/StatusPill";
import { usePersistentState } from "../../ui/usePersistentState";
import { IdentityDialog, UploadDialog, type UploadTarget } from "./DocumentDialogs";
import {
  ACTIVE,
  EXTRACTION_KEY,
  STATUS,
  WORK_STATE,
  facets,
  needsAttention,
  workRun,
  type Status,
} from "./preparation";
import { OperationPanel } from "./OperationPanel";

type Doc = Schemas["DocumentRow"];
type Work = Schemas["DocumentWork"];
type SortKey = "name" | "role" | "version" | "status";

const ROLE: Record<Doc["role"], string> = { main: "Основной текст", supplement: "Приложение" };
const ATTENTION: Status[] = ["not_found", "needs_review", "error"];

const SORT_VALUE: Record<SortKey, (d: Doc) => string | number> = {
  name: (d) => d.name,
  role: (d) => ROLE[d.role],
  version: (d) => d.version ?? 0,
  status: (d) => STATUS[d.status].label,
};

const demo = isMockApi ? "Демо: " : "";
const works = (n: number) => plural(n, ["работа", "работы", "работ"]);
/** Согласование глагола с числом: «1 готов», «4 готовы». */
const agree = (n: number, one: string, many: string) =>
  `${n} ${n % 10 === 1 && n % 100 !== 11 ? one : many}`;

export function PreparationPage() {
  const extraction = useQuery({
    queryKey: EXTRACTION_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/extraction");
      if (!data) throw new Error("Извлечение не загружено");
      return data;
    },
    refetchInterval: (query) => {
      const data = query.state.data;
      const busy =
        data &&
        (ACTIVE.includes(data.operation.status) ||
          data.documents.some((d) => d.status === "fetching"));
      return busy ? 500 : false;
    },
  });
  if (extraction.isError) {
    return <LoadFailed what="документы и ход извлечения" onRetry={() => extraction.refetch()} />;
  }
  if (!extraction.data) return null;
  return <PreparationView data={extraction.data} />;
}

function PreparationView({ data }: { data: Schemas["Extraction"] }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [text, setText] = usePersistentState("extraction:text", "");
  const [statusFilter, setStatusFilter] = usePersistentState<Status | "all">(
    "extraction:status",
    "all",
  );
  const [sort, setSort] = usePersistentState<{ key: SortKey; dir: 1 | -1 } | null>(
    "extraction:sort",
    null,
  );
  // Диалоги держат id: данные документа берутся из свежего ответа, а не из снимка при открытии.
  const [upload, setUpload] = useState<{ kind: UploadTarget["kind"]; id: string } | null>(null);
  // Ручное раскрытие работ; без него раскрыты работы, где пользователю есть что делать.
  const [expanded, setExpanded] = usePersistentState<Record<string, boolean>>(
    "extraction:expanded",
    {},
  );
  const [identityId, setIdentityId] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const operationTitle = useRef<HTMLParagraphElement>(null);
  const stopConfirmed = useRef(false);
  const startKey = useRef<string | null>(null);

  const { documents, operation } = data;
  const active = ACTIVE.includes(operation.status);
  const attention = documents.filter((d) => ATTENTION.includes(d.status)).length;
  const ready = documents.filter((d) => d.status === "ready").length;
  const startable = data.startable_publication_ids;
  const find = (id: string | null) => documents.find((d) => d.id === id) ?? null;
  const identityFor = find(identityId);
  let uploadTarget: UploadTarget | null = null;
  if (upload?.kind === "document") {
    const document = find(upload.id);
    if (document && can(document, "upload")) uploadTarget = { kind: "document", document };
  } else if (upload?.kind === "supplement") {
    const work = data.works.find((w) => w.publication_id === upload.id);
    if (work?.allowed_actions.includes("upload_supplement")) {
      uploadTarget = { kind: "supplement", work };
    }
  }

  const visible = useMemo(() => {
    const q = text.trim().toLowerCase();
    const rows = documents.filter(
      (d) =>
        (!q || `${d.name} ${d.publication_label}`.toLowerCase().includes(q)) &&
        (statusFilter === "all" || d.status === statusFilter),
    );
    if (!sort) return rows;
    const value = SORT_VALUE[sort.key];
    return [...rows].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const order =
        typeof x === "string" && typeof y === "string" ? x.localeCompare(y, "ru") : +x - +y;
      return order * sort.dir;
    });
  }, [documents, text, statusFilter, sort]);

  // Работа, раскрытая по необходимости, не сворачивается сама, когда всё готово: строки не
  // исчезают из-под глаз; свернуть её может только пользователь.
  useEffect(() => {
    const opened = data.works.filter(
      (work) => expanded[work.publication_id] === undefined && needsAttention(work),
    );
    if (opened.length) {
      setExpanded({
        ...expanded,
        ...Object.fromEntries(opened.map((work) => [work.publication_id, true])),
      });
    }
  }, [data.works, expanded, setExpanded]);

  // Поиск и фильтр отбирают документы, но сохраняют строку их работы (shape §3.5).
  const filtering = text.trim() !== "" || statusFilter !== "all";
  const groups = data.works
    .map((work) => ({
      work,
      docs: visible.filter((d) => d.publication_id === work.publication_id),
    }))
    .filter((group) => !filtering || group.docs.length > 0);
  // При поиске и фильтре совпадения всегда видны: переключатель работы тогда недоступен.
  const isOpen = (work: Work) =>
    filtering || (expanded[work.publication_id] ?? needsAttention(work));

  /** Выполняет команду; возвращает null или текст причины отказа. */
  async function attempt(
    request: () => Promise<{ error?: unknown; response: Response }>,
    failure: string,
  ): Promise<string | null> {
    // Ответ опроса, начатого до команды, не должен перекрыть её результат.
    await queryClient.cancelQueries({ queryKey: EXTRACTION_KEY });
    try {
      const { error, response } = await request();
      return response.ok ? null : `${failure}: ${failureReason(error)}.`;
    } catch {
      return `${failure}: сервер недоступен.`;
    } finally {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: EXTRACTION_KEY }),
        queryClient.invalidateQueries({ queryKey: STUDY_KEY }),
      ]);
    }
  }

  async function command(path: "pause" | "resume" | "stop" | "retry-failed", failure: string) {
    setMessage(await attempt(() => api.POST(`/api/extraction/${path}`), failure));
  }

  async function start() {
    // Один ключ на попытку запуска: повтор после сбоя сети не создаст вторую операцию.
    // Не `crypto.randomUUID()`: его нет вне HTTPS и localhost, а пилот открывают по http://IP.
    startKey.current ??= Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");
    // Частично прочитанный основной текст тоже входит: он пригоден к обработке.
    const versions = documents
      .filter(
        (d) =>
          startable.includes(d.publication_id) &&
          d.version &&
          d.identity_status === "confirmed" &&
          (d.parse_status === "parsed" || d.parse_status === "partial"),
      )
      .map((d) => ({ document_id: d.id, version: d.version! }));
    const failure = await attempt(
      () =>
        api.POST("/api/extraction/start", {
          params: { header: { "idempotency-key": startKey.current! } },
          body: { publication_ids: startable, document_versions: versions },
        }),
      "Извлечение не запущено",
    );
    if (!failure) startKey.current = null;
    setMessage(failure);
  }

  function header(key: SortKey, label: string) {
    const current = sort?.key === key;
    return (
      <th
        className="border-r border-line px-4 py-3 font-normal"
        aria-sort={current ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}
      >
        <button
          type="button"
          className="inline-flex items-center gap-1 hover:text-ink"
          onClick={() => setSort({ key, dir: current && sort!.dir === 1 ? -1 : 1 })}
        >
          {label}
          <ArrowUpDown aria-hidden className={`size-3.5 ${current ? "text-accent" : ""}`} />
        </button>
      </th>
    );
  }

  const lockedReason = active
    ? "Документы нельзя менять, пока извлечение выполняется или на паузе: остановите его."
    : null;
  const hasResult = operation.works.some((w) => w.execution === "done");
  // Шаг «Результат» открывает worker, а не команда клиента: прогресс исследования перечитывается.
  useEffect(() => {
    if (hasResult) void queryClient.invalidateQueries({ queryKey: STUDY_KEY });
  }, [hasResult, queryClient]);
  const unfinished = operation.works.filter(
    (w) => w.execution !== "done" || w.outcome !== "measures_extracted",
  ).length;
  const notStarted = operation.status === "not_started";
  // Подзаголовок называет текущий этап подготовки: загрузку или извлечение (shape §3.5).
  const subtitle = notStarted
    ? "Загрузите документы выбранных работ и запустите извлечение показателей."
    : active
      ? "Идёт извлечение показателей: документы доступны только для чтения."
      : "Извлечение завершено: откройте результат.";

  let statusText: string | null = null;
  if (notStarted) {
    statusText =
      data.start_blocked_reason ??
      `Готово к извлечению: ${demo}${works(startable.length)}. Недоступные документы останутся видимыми в охвате.`;
  } else if (hasResult && unfinished > 0) {
    statusText = `Текущий результат неполный: ${works(unfinished)} без показателей, с ошибкой или не обработаны.`;
  }

  return (
    <>
      <Panel className="pt-5">
        <div className="px-6">
          <PageHeader title="Подготовка" subtitle={subtitle} />
          <p className="mt-1 text-ink-muted">
            {demo}
            {plural(documents.length, ["документ", "документа", "документов"])} ·{" "}
            {agree(ready, "готов", "готовы")} · {agree(attention, "требует", "требуют")} внимания
          </p>
        </div>

        {!notStarted && (
          <div className="mt-4">
            <OperationPanel
              op={operation}
              titleRef={operationTitle}
              onPause={() => command("pause", "Пауза не запрошена")}
              onResume={() => command("resume", "Извлечение не продолжено")}
              onStop={() => setConfirmStop(true)}
              onRetryFailed={() => command("retry-failed", "Повтор не запущен")}
            />
          </div>
        )}

        {message && (
          <p role="alert" className="mx-6 mt-3 rounded-control bg-warning-soft px-4 py-2">
            {message}
          </p>
        )}

        <div className="mt-4 flex items-center gap-3 border-t border-line px-6 py-4">
          <label className="relative max-w-xl flex-1">
            <Search
              aria-hidden
              className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-muted"
            />
            <input
              aria-label="Поиск по документам и публикациям"
              placeholder="Поиск по документам и публикациям…"
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="w-full rounded-control border border-line py-2 pr-3 pl-9 outline-none focus:border-accent"
            />
          </label>
          <label className="relative">
            <ListFilter
              aria-hidden
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-muted"
            />
            <select
              aria-label="Статус документа"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as Status | "all")}
              className="rounded-control border border-line bg-surface py-2 pr-3 pl-9 outline-none focus:border-accent"
            >
              <option value="all">Все статусы</option>
              {(Object.keys(STATUS) as Status[]).map((s) => (
                <option key={s} value={s}>
                  {STATUS[s].label}
                </option>
              ))}
            </select>
          </label>
          {visible.length < documents.length && (
            <span className="text-ink-muted">
              Скрыто фильтрами: {documents.length - visible.length}
            </span>
          )}
        </div>

        {lockedReason && (
          <p id="documents-locked" className="px-6 pb-2 text-caption text-ink-muted">
            {lockedReason}
          </p>
        )}

        <table
          aria-label="Документы"
          className="w-full border-collapse border-t border-line text-left"
        >
          <thead className="text-ink-muted">
            <tr>
              {header("name", "Документ")}
              {header("role", "Тип")}
              {header("version", "Версия")}
              {header("status", "Статус")}
              <th className="px-4 py-3 font-normal">Действия</th>
            </tr>
          </thead>
          <tbody>
            {groups.map(({ work, docs }) => {
              const open = isOpen(work);
              return [
                <WorkRow
                  key={`work-${work.publication_id}`}
                  work={work}
                  run={operation.works.find((w) => w.publication_id === work.publication_id)}
                  open={open}
                  filtering={filtering}
                  locked={lockedReason ? "documents-locked" : undefined}
                  onToggle={() => setExpanded({ ...expanded, [work.publication_id]: !open })}
                  onAddSupplement={() => setUpload({ kind: "supplement", id: work.publication_id })}
                />,
                ...(open ? docs : []).map((doc) => {
                  const can = (action: Doc["allowed_actions"][number]) =>
                    doc.allowed_actions.includes(action);
                  const locked = lockedReason ? "documents-locked" : undefined;
                  // Открытого текста нет: повтор даст тот же ответ, главное — ручная загрузка.
                  const retryLast = doc.fetch_status === "unavailable";
                  // Приложение, добавленное вручную, загружать из источника неоткуда; нечитаемый
                  // формат не загружается вовсе — сервер не предлагает повтор. Пока идёт
                  // загрузка или операция, кнопка видна, но недоступна.
                  const retry = doc.fetch_status !== null &&
                    (can("retry_fetch") || locked || doc.status === "fetching") && (
                      <Button
                        variant={retryLast ? "text" : "link"}
                        aria-label={`Повторить загрузку «${doc.name}»`}
                        aria-describedby={locked}
                        disabled={!can("retry_fetch")}
                        onClick={async () =>
                          setMessage(
                            await attempt(
                              () =>
                                api.POST("/api/documents/{document_id}/retry-fetch", {
                                  params: { path: { document_id: doc.id } },
                                }),
                              "Загрузка не запущена",
                            ),
                          )
                        }
                      >
                        <RefreshCw aria-hidden className="size-4" />
                        Повторить загрузку
                      </Button>
                    );
                  return (
                    <tr
                      key={doc.id}
                      className={`border-t border-line ${ATTENTION.includes(doc.status) ? "bg-warning-soft/60" : ""}`}
                    >
                      <td className="border-r border-line px-4 py-2.5">
                        <div className="flex items-center gap-3 pl-8">
                          <File aria-hidden className="size-6 shrink-0 text-ink-muted" />
                          <p className="font-medium">{doc.name}</p>
                        </div>
                      </td>
                      <td className="border-r border-line px-4 py-2.5">{ROLE[doc.role]}</td>
                      <td className="border-r border-line px-4 py-2.5">
                        {doc.version ? `v${doc.version}` : "—"}
                      </td>
                      <td className="border-r border-line px-4 py-2.5">
                        <StatusPill tone={STATUS[doc.status].tone}>
                          {STATUS[doc.status].label}
                        </StatusPill>
                        {doc.status_detail && (
                          <p className="mt-1 max-w-xs text-caption text-ink-muted">
                            {doc.status_detail}
                          </p>
                        )}
                        <p className="mt-1 max-w-xs text-caption text-ink-muted">{facets(doc)}</p>
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
                          {can("confirm_identity") && (
                            <Button
                              variant="link"
                              aria-label={`Проверить документ «${doc.name}»`}
                              onClick={() => setIdentityId(doc.id)}
                            >
                              <ShieldCheck aria-hidden className="size-4" />
                              Проверить
                            </Button>
                          )}
                          {doc.source_url && (
                            <a
                              href={doc.source_url}
                              target="_blank"
                              rel="noreferrer"
                              aria-label={`Открыть PDF «${doc.name}» у источника (открывается в новой вкладке)`}
                              className="inline-flex items-center gap-2 font-medium text-accent hover:text-accent-strong"
                            >
                              <ExternalLink aria-hidden className="size-4" />
                              Открыть PDF
                            </a>
                          )}
                          {!retryLast && retry}
                          <Button
                            variant="link"
                            aria-label={`Загрузить вручную «${doc.name}»`}
                            aria-describedby={locked}
                            disabled={!can("upload")}
                            onClick={() => setUpload({ kind: "document", id: doc.id })}
                          >
                            <Upload aria-hidden className="size-4" />
                            Загрузить вручную
                          </Button>
                          {retryLast && retry}
                        </div>
                      </td>
                    </tr>
                  );
                }),
              ];
            })}
          </tbody>
        </table>
      </Panel>

      <BottomBar
        status={
          statusText && (
            <span id="extraction-status" className="flex items-center gap-2">
              <Info aria-hidden className="size-5 shrink-0" />
              {statusText}
            </span>
          )
        }
      >
        <Button onClick={() => navigate("/selection")}>Назад к отбору</Button>
        {operation.allowed_actions.includes("start") || notStarted ? (
          <Button
            variant="primary"
            disabled={!operation.allowed_actions.includes("start")}
            aria-describedby={statusText ? "extraction-status" : undefined}
            onClick={start}
          >
            {notStarted ? "Запустить извлечение" : "Запустить необработанные"} · {demo}
            {works(startable.length)}
            <ArrowRight aria-hidden className="size-4" />
          </Button>
        ) : null}
        {!notStarted && (
          <Button
            variant={operation.allowed_actions.includes("start") ? "secondary" : "primary"}
            disabled={!hasResult}
            aria-describedby={statusText ? "extraction-status" : undefined}
            onClick={async () => {
              await queryClient.invalidateQueries({ queryKey: STUDY_KEY });
              navigate("/result");
            }}
          >
            Открыть текущий результат
            <ArrowRight aria-hidden className="size-4" />
          </Button>
        )}
      </BottomBar>

      <UploadDialog
        target={uploadTarget}
        onClose={() => setUpload(null)}
        onUpload={(file) =>
          attempt(
            () =>
              upload?.kind === "supplement"
                ? api.POST("/api/publications/{publication_id}/supplements", {
                    params: { path: { publication_id: upload!.id } },
                    ...fileBody(file),
                  })
                : api.POST("/api/documents/{document_id}/upload", {
                    params: { path: { document_id: upload!.id } },
                    ...fileBody(file),
                  }),
            "Документ не загружен",
          )
        }
      />
      <IdentityDialog
        document={identityFor && can(identityFor, "confirm_identity") ? identityFor : null}
        onClose={() => setIdentityId(null)}
        onDecide={(belongs) =>
          attempt(
            () =>
              api.POST("/api/documents/{document_id}/identity", {
                params: { path: { document_id: identityId! } },
                body: { belongs_to_publication: belongs },
              }),
            "Решение не сохранено",
          )
        }
      />
      <Modal
        open={confirmStop}
        onClose={() => setConfirmStop(false)}
        title="Остановить извлечение?"
        description="Текущая работа завершится, оставшиеся не будут обработаны. Готовые результаты и ваши исправления сохранятся; необработанные работы можно будет запустить снова."
        width="w-[34rem]"
        onCloseAutoFocus={(event) => {
          // После остановки кнопка исчезает — фокус на статус операции; при отмене Radix
          // сам вернёт фокус на «Остановить».
          if (!stopConfirmed.current) return;
          event.preventDefault();
          stopConfirmed.current = false;
          operationTitle.current?.focus();
        }}
      >
        <div className="mt-5 flex justify-end gap-3">
          <Button onClick={() => setConfirmStop(false)}>Продолжить работу</Button>
          <Button
            variant="primary"
            onClick={() => {
              stopConfirmed.current = true;
              setConfirmStop(false);
              void command("stop", "Остановка не запрошена");
            }}
          >
            Остановить
          </Button>
        </div>
      </Modal>
    </>
  );
}

const can = (doc: Doc, action: Doc["allowed_actions"][number]) =>
  doc.allowed_actions.includes(action);

/** В схеме OpenAPI двоичный файл описан как string; отправляем FormData,
 * openapi-fetch при этом не ставит JSON Content-Type. */
const fileBody = (file: File) => ({
  body: { file: file as unknown as string },
  bodySerializer: (body: { file: string }) => {
    const form = new FormData();
    form.append("file", body.file as unknown as Blob);
    return form;
  },
});

/** Строка работы над её документами: состояние, пробелы и действие работы. */
function WorkRow({
  work,
  run,
  open,
  filtering,
  locked,
  onToggle,
  onAddSupplement,
}: {
  work: Work;
  // Итог обработки работы; до её первого запуска — состояние документов (shape §3.5).
  run: Schemas["WorkRun"] | undefined;
  open: boolean;
  filtering: boolean;
  locked: string | undefined;
  onToggle: () => void;
  onAddSupplement: () => void;
}) {
  const state = run ? workRun(run) : WORK_STATE[work.state];
  // Готовая работа с пробелами не выглядит зелёной: пробелы не прячутся за «готово».
  const tone = state.tone === "success" && work.gaps.length ? "warning" : state.tone;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <tr className="border-t border-line bg-neutral-soft/50">
      <td colSpan={4} className="border-r border-line px-4 py-2.5">
        <button
          type="button"
          aria-expanded={open}
          disabled={filtering}
          onClick={onToggle}
          className="flex items-start gap-2 text-left hover:text-accent"
        >
          <Chevron aria-hidden className="mt-0.5 size-5 shrink-0 text-ink-muted" />
          <span>
            <span className="font-medium">{work.label}</span>
            <span className="text-ink-muted">
              {" "}
              · {plural(work.document_count, ["документ", "документа", "документов"])}
            </span>
          </span>
        </button>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 pl-7">
          <StatusPill tone={tone}>{state.label}</StatusPill>
          {run?.detail && <span className="text-caption text-ink-muted">{run.detail}</span>}
          {work.gaps.map((gap) => (
            <span key={gap} className="text-caption text-warning-ink">
              {gap}
            </span>
          ))}
        </div>
      </td>
      <td className="px-4 py-2.5">
        <Button
          variant="link"
          aria-label={`Загрузить приложение к работе «${work.label}»`}
          aria-describedby={locked}
          disabled={!work.allowed_actions.includes("upload_supplement")}
          onClick={onAddSupplement}
        >
          <FilePlus aria-hidden className="size-4" />
          Загрузить приложение
        </Button>
      </td>
    </tr>
  );
}
