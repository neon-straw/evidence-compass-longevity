import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { api } from "../../api/client";
import { failureReason } from "../../api/errors";
import { resultQuery } from "./format";

/** «Экспорт CSV» в шапке экрана «Результат»: выгружает ту же ревизию таблицы, что на экране. */
export function ExportCsvButton() {
  const result = useQuery(resultQuery);
  const [error, setError] = useState<string | null>(null);
  const revision = result.data?.revision;
  const allowed = result.data?.allowed_actions.includes("export") ?? false;

  async function download() {
    if (!revision) return;
    setError(null);
    try {
      const { data, error: failure } = await api.GET("/api/result/export", {
        params: { query: { revision: revision.number } },
        parseAs: "text",
      });
      if (data === undefined) {
        // С parseAs "text" тело ошибки приходит строкой: достаём Problem.message из JSON.
        let problem: unknown = failure;
        try {
          problem = typeof failure === "string" ? JSON.parse(failure) : failure;
        } catch {
          // Не JSON — показываем общую причину.
        }
        setError(`CSV не выгружен: ${failureReason(problem)}.`);
        return;
      }
      const url = URL.createObjectURL(new Blob([data], { type: "text/csv;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `evidence-compass-revision-${revision.number}${revision.freshness === "stale" ? "-stale" : ""}.csv`;
      link.click();
      // Некоторые браузеры отменяют загрузку, если ссылку отозвать сразу после клика.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError("CSV не выгружен: сервер недоступен.");
    }
  }

  return (
    <>
      {error && (
        <span role="alert" className="text-caption text-warning-ink">
          {error}
        </span>
      )}
      <button
        type="button"
        disabled={!allowed}
        onClick={download}
        className="flex items-center gap-1.5 text-ink-muted hover:text-ink disabled:opacity-50"
        title={
          revision
            ? `Ревизия ${revision.number}${revision.freshness === "stale" ? ", устаревшая" : ""}`
            : undefined
        }
      >
        <Download aria-hidden className="size-4" />
        Экспорт CSV
      </button>
    </>
  );
}
