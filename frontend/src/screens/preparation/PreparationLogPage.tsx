import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router";
import { api } from "../../api/client";
import { Button } from "../../ui/Button";
import { LoadFailed } from "../../ui/LoadFailed";
import { PageHeader, Panel } from "../../ui/Page";
import { ACTIVE, EXTRACTION_KEY } from "./preparation";

/** Подробный журнал операции — отдельное представление (shape §3.5, §7). */
export function PreparationLogPage() {
  const navigate = useNavigate();
  const operation = useQuery({
    queryKey: EXTRACTION_KEY,
    queryFn: async () => (await api.GET("/api/extraction")).data ?? null,
    refetchInterval: (query) =>
      query.state.data && ACTIVE.includes(query.state.data.operation.status) ? 1000 : false,
  });
  const running = operation.data ? ACTIVE.includes(operation.data.operation.status) : false;
  const log = useQuery({
    queryKey: ["extraction-log"],
    queryFn: async () => {
      const { data } = await api.GET("/api/extraction/log");
      if (!data) throw new Error("Журнал не загружен");
      return data;
    },
    // Пока операция идёт, журнал пополняется.
    refetchInterval: running ? 1000 : false,
  });

  return (
    <Panel className="px-6 py-5">
      <PageHeader
        title="Журнал операции"
        subtitle="События извлечения по порядку: запуск, работы, пауза, остановка, повторы."
        aside={
          <Button onClick={() => navigate("/preparation")}>
            <ArrowLeft aria-hidden className="size-4" />
            Вернуться к подготовке
          </Button>
        }
      />
      {log.isError && <LoadFailed what="журнал операции" onRetry={() => log.refetch()} />}
      {log.data && log.data.length === 0 && (
        <p className="mt-6 text-ink-muted">Событий пока нет: извлечение не запускалось.</p>
      )}
      {log.data && log.data.length > 0 && (
        <ol aria-label="События" className="mt-6 space-y-2 border-t border-line pt-4">
          {log.data.map((event, i) => (
            <li key={i} className="flex gap-4">
              <time dateTime={event.at} className="w-24 shrink-0 text-ink-muted">
                {new Date(event.at).toLocaleTimeString("ru-RU")}
              </time>
              <span>{event.message}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
