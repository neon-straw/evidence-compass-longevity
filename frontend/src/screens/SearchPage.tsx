import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleMinus } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, isMockApi } from "../api/client";
import { PROVENANCE_LABEL } from "../api/labels";
import type { Schemas, SourceStatus } from "../api/types";
import { STUDY_KEY } from "../app/useReachedStep";
import { Button } from "../ui/Button";
import { LoadFailed } from "../ui/LoadFailed";
import { Modal } from "../ui/Modal";
import { BottomBar, Panel } from "../ui/Page";
import { plural } from "../ui/plural";
import { StatusPill, type Tone } from "../ui/StatusPill";

type Run = Schemas["SearchRun"];
type Source = Schemas["SourceRun"];

const SEARCH_KEY = ["search"] as const;

const SOURCE_STATUS: Record<SourceStatus, { tone: Tone; label: string }> = {
  pending: { tone: "neutral", label: "Ожидает" },
  running: { tone: "progress", label: "Выполняется" },
  hits: { tone: "success", label: "Завершено" },
  empty: { tone: "success", label: "Завершено" },
  error: { tone: "warning", label: "Ошибка" },
  stopped: { tone: "neutral", label: "Остановлено" },
};

// Число записей — демо-значение, пока клиент работает на MSW.
const records = (n: number) =>
  `${isMockApi ? "Демо: " : ""}${plural(n, ["запись", "записи", "записей"])}`;

function SourceResult({ source }: { source: Source }) {
  const [open, setOpen] = useState(false);
  switch (source.status) {
    case "pending":
      return "Не запускался";
    case "running":
      return "Ожидаем ответ";
    case "hits":
    case "empty":
      if (source.returned_count !== null && source.returned_count < (source.hit_count ?? 0)) {
        return (
          <>
            <span>
              {isMockApi ? "Демо: " : ""}Найдено {source.hit_count}, сохранено{" "}
              {source.returned_count}
            </span>
            <p className="mt-1 text-caption text-warning-ink">
              Получена только часть выдачи источника.
            </p>
          </>
        );
      }
      return records(source.hit_count ?? 0);
    case "stopped":
      return "Остановлен до ответа";
    case "error":
      return (
        <>
          <span>Ресурс не ответил. </span>
          <Button variant="link" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Скрыть диагностику" : "Показать диагностику"}
          </Button>
          {open && <p className="mt-1 text-caption text-warning-ink">{source.error}</p>}
        </>
      );
  }
}

export function SearchPage() {
  const search = useQuery({
    queryKey: SEARCH_KEY,
    queryFn: async () => {
      const { data } = await api.GET("/api/search");
      if (!data) throw new Error("Поиск не загружен");
      return data;
    },
    refetchInterval: (query) => (query.state.data?.status === "running" ? 500 : false),
  });
  if (search.isError) return <LoadFailed what="поиск" onRetry={() => search.refetch()} />;
  if (!search.data) return null;
  return <SearchView run={search.data} />;
}

function SearchView({ run }: { run: Run }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [confirmStop, setConfirmStop] = useState(false);
  const [openQuery, setOpenQuery] = useState<Source | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const operationTitle = useRef<HTMLParagraphElement>(null);

  // Поиск завершается в фоне: когда отбор стал доступен, обновляем прогресс исследования.
  const selectionOpen = run.selection_blocked_reason === null;
  useEffect(() => {
    if (selectionOpen) void queryClient.invalidateQueries({ queryKey: STUDY_KEY });
  }, [selectionOpen, queryClient]);

  async function act(request: () => Promise<{ data?: Run; error?: unknown }>, failure: string) {
    setActionError(null);
    // Ответ опроса, начатого до команды, не должен перекрыть её результат.
    await queryClient.cancelQueries({ queryKey: SEARCH_KEY });
    try {
      const { data, error } = await request();
      if (!data) {
        // Отказ сервера (слот занят, критерии не выражаются в запросе) показывается как есть.
        const reason = error && typeof error === "object" && "message" in error && error.message;
        setActionError(
          typeof reason === "string"
            ? `${failure} ${reason}`
            : `${failure} Сервер недоступен; сохранённые результаты не изменены.`,
        );
        return;
      }
      queryClient.setQueryData(SEARCH_KEY, data);
      await queryClient.invalidateQueries({ queryKey: STUDY_KEY });
    } catch {
      setActionError(`${failure} Сервер недоступен; сохранённые результаты не изменены.`);
    }
  }
  const start = () => act(() => api.POST("/api/search/start"), "Не удалось запустить поиск.");
  const stop = () => act(() => api.POST("/api/search/stop"), "Не удалось остановить поиск.");

  return (
    <>
      <Panel>
        <h1 className="border-b border-line px-6 py-4 text-title font-semibold">
          Поиск публикаций
        </h1>
        <OperationBlock
          run={run}
          titleRef={operationTitle}
          onStart={start}
          onStop={() => setConfirmStop(true)}
          onEditCriteria={() => navigate("/criteria")}
        />
        {actionError && (
          <p role="alert" className="mx-6 mb-4 rounded-control bg-warning-soft px-4 py-3">
            {actionError}
          </p>
        )}
      </Panel>

      <Panel className="px-6 py-5">
        <h2 className="text-xl font-semibold">Ресурсы</h2>
        <div className="mt-3 overflow-hidden rounded-control border border-line">
          <table className="w-full border-collapse text-left">
            <thead className="bg-canvas text-ink-muted">
              <tr>
                {["Ресурс", "Запрос", "Состояние", "Результат"].map((h) => (
                  <th key={h} className="border-r border-line px-4 py-3 font-normal">
                    {h}
                  </th>
                ))}
                <th className="px-4 py-3">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {run.sources.map((source) => (
                <tr key={source.resource} className="border-t border-line">
                  <th scope="row" className="border-r border-line px-4 py-3 text-lg font-semibold">
                    {PROVENANCE_LABEL[source.resource]}
                  </th>
                  <td className="max-w-md border-r border-line px-4 py-3 text-ink-muted">
                    <span className="line-clamp-2 break-words">{source.query}</span>
                  </td>
                  <td className="border-r border-line px-4 py-3">
                    <StatusPill tone={SOURCE_STATUS[source.status].tone}>
                      {SOURCE_STATUS[source.status].label}
                    </StatusPill>
                  </td>
                  <td className="border-r border-line px-4 py-3">
                    <SourceResult source={source} />
                  </td>
                  <td className="px-4 py-3">
                    <Button
                      variant="link"
                      aria-label={`Открыть запрос ${PROVENANCE_LABEL[source.resource]}`}
                      onClick={() => setOpenQuery(source)}
                    >
                      Открыть запрос
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <BottomBar>
        <Button onClick={() => navigate("/criteria")}>Назад к критериям</Button>
        <div className="flex flex-col items-end gap-1">
          <Button
            variant="primary"
            disabled={!selectionOpen}
            aria-describedby="selection-blocked"
            onClick={() => navigate("/selection")}
          >
            Перейти к отбору
          </Button>
          <span id="selection-blocked" className="text-caption text-ink-muted empty:hidden">
            {run.selection_blocked_reason}
          </span>
        </div>
      </BottomBar>

      <Modal
        open={confirmStop}
        onClose={() => setConfirmStop(false)}
        title="Остановить поиск?"
        description="Ресурсы, которые ещё не ответили, будут отмечены как остановленные. Уже полученные результаты сохранятся; для полного результата поиск нужно будет повторить."
        width="w-[32rem]"
        onCloseAutoFocus={(event) => {
          // Кнопка остановки исчезает вместе с запуском: фокус переходим на статус операции.
          event.preventDefault();
          operationTitle.current?.focus();
        }}
      >
        <div className="mt-5 flex justify-end gap-3">
          <Button onClick={() => setConfirmStop(false)}>Продолжить поиск</Button>
          <Button
            variant="primary"
            onClick={() => {
              setConfirmStop(false);
              void stop();
            }}
          >
            Остановить
          </Button>
        </div>
      </Modal>
      <Modal
        open={openQuery !== null}
        onClose={() => setOpenQuery(null)}
        title={`Запрос ${openQuery ? PROVENANCE_LABEL[openQuery.resource] : ""}`}
        description={`Фактический запрос по подтверждённым критериям. Термины ищутся в названии и аннотации.${openQuery?.resource === "pubmed" ? " PubMed также учитывает авторские ключевые слова." : ""}`}
        width="w-[44rem]"
      >
        <pre className="mt-4 max-h-80 overflow-auto rounded-control bg-canvas p-4 text-caption break-words whitespace-pre-wrap">
          {openQuery?.query}
        </pre>
        <div className="mt-5 flex justify-end">
          <Button onClick={() => setOpenQuery(null)}>Закрыть</Button>
        </div>
      </Modal>
    </>
  );
}

function OperationBlock({
  run,
  titleRef,
  onStart,
  onStop,
  onEditCriteria,
}: {
  run: Run;
  titleRef: React.RefObject<HTMLParagraphElement | null>;
  onStart: () => void;
  onStop: () => void;
  onEditCriteria: () => void;
}) {
  const statuses = run.sources.map((s) => s.status);
  const failed = run.sources.filter((s) => s.status === "error");
  const limited = run.sources.filter(
    (s) => s.returned_count !== null && s.hit_count !== null && s.returned_count < s.hit_count,
  );
  const saved = `Результаты сохранены для версии критериев ${run.criteria_version}.`;
  const allowed = (action: Run["allowed_actions"][number]) => run.allowed_actions.includes(action);

  let icon: ReactNode = null;
  let title: string;
  let text: string;
  let editCriteria = false;

  if (run.status === "not_started") {
    title = "Поиск ещё не запускался";
    text = `Запросы PubMed и Europe PMC построены из подтверждённых критериев версии ${run.criteria_version}.`;
  } else if (run.status === "running") {
    title = "Поиск выполняется";
    text = "Можно закрыть браузер — поиск продолжится.";
  } else if (run.status === "stopped") {
    icon = <CircleMinus aria-hidden className="size-12 fill-ink-muted text-surface" />;
    title = "Поиск остановлен";
    text = "Ресурсы, которые не успели ответить, отмечены как остановленные. Повторите поиск.";
  } else if (failed.length === statuses.length) {
    icon = <CircleAlert aria-hidden className="size-12 fill-warning text-surface" />;
    title = "Поиск не выполнен";
    text = "Все ресурсы завершились ошибкой, записей нет. Повторите поиск или измените критерии.";
    editCriteria = true;
  } else if (failed.length > 0) {
    icon = <CircleAlert aria-hidden className="size-12 fill-warning text-surface" />;
    title = "Поиск завершён частично";
    text = `${failed.map((s) => PROVENANCE_LABEL[s.resource]).join(", ")}: ошибка. Найденное остальными ресурсами доступно для отбора. ${saved}`;
  } else if (statuses.every((s) => s === "empty")) {
    icon = <CircleCheck aria-hidden className="size-12 fill-success text-surface" />;
    title = "Поиск завершён: записей не найдено";
    text =
      "Ресурсы ответили без ошибок и вернули 0 записей. Измените критерии или добавьте известную работу вручную на шаге отбора.";
    editCriteria = true;
  } else {
    icon = <CircleCheck aria-hidden className="size-12 fill-success text-surface" />;
    title = "Поиск завершён";
    text = saved;
  }

  return (
    <div className="flex items-center gap-5 px-6 py-5">
      {icon}
      <div role="status" className="min-w-64">
        <p ref={titleRef} tabIndex={-1} className="text-xl font-semibold outline-none">
          {title}
        </p>
        <p className="mt-1 text-ink-muted">{text}</p>
        {limited.length > 0 && (
          <p className="mt-2 text-warning-ink">
            Неполный охват: {limited.map((s) => PROVENANCE_LABEL[s.resource]).join(", ")} —
            сохранена только часть найденных записей. Для отбора доступна сохранённая часть.
          </p>
        )}
      </div>
      {run.status === "running" && (
        <div
          role="progressbar"
          aria-label="Ход поиска"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(run.progress * 100)}
          className="mx-8 h-2.5 flex-1 overflow-hidden rounded-full bg-neutral-soft"
        >
          <div
            className="h-full rounded-full bg-accent/70 transition-[width]"
            style={{ width: `${Math.max(8, run.progress * 100)}%` }}
          />
        </div>
      )}
      <div className="ml-auto flex shrink-0 gap-3">
        {editCriteria && <Button onClick={onEditCriteria}>Изменить критерии</Button>}
        {allowed("start") && (
          <Button variant="primary" onClick={onStart}>
            Запустить поиск
          </Button>
        )}
        {allowed("stop") && <Button onClick={onStop}>Остановить поиск</Button>}
        {allowed("retry") && <Button onClick={onStart}>Повторить поиск</Button>}
      </div>
    </div>
  );
}
