import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { LoadFailed } from "../../ui/LoadFailed";
import { PageHeader, Panel } from "../../ui/Page";
import { StatusPill, type Tone } from "../../ui/StatusPill";
import { resultQuery } from "./format";

const OUTCOME: Record<
  NonNullable<Schemas["CoverageWork"]["outcome"]>,
  { tone: Tone; label: string }
> = {
  measures_extracted: { tone: "success", label: "Показатели извлечены" },
  no_measures: { tone: "neutral", label: "Без показателей" },
  unresolved: { tone: "warning", label: "Требует решения" },
  unavailable: { tone: "neutral", label: "Документ недоступен" },
  error: { tone: "warning", label: "Ошибка обработки" },
};

/** Подробный охват — отдельное представление (shape §3.6): что вошло в результат и почему нет. */
export function CoveragePage() {
  const navigate = useNavigate();
  const result = useQuery(resultQuery);
  if (result.isError) return <LoadFailed what="охват" onRetry={() => result.refetch()} />;
  if (!result.data) return null;
  const { coverage } = result.data;

  return (
    <Panel className="px-6 py-5">
      <PageHeader
        title="Охват результата"
        subtitle={`Найдено ${coverage.found}, отобрано ${coverage.selected}, полных текстов ${coverage.full_texts}, недоступно ${coverage.unavailable}.`}
        aside={
          <Button onClick={() => navigate("/result")}>
            <ArrowLeft aria-hidden className="size-4" />
            Вернуться к результату
          </Button>
        }
      />
      <table aria-label="Охват по работам" className="mt-5 w-full border-collapse text-left">
        <thead className="border-y border-line text-ink-muted">
          <tr>
            <th className="px-3 py-2.5 font-normal">Работа</th>
            <th className="px-3 py-2.5 font-normal">Итог</th>
            <th className="px-3 py-2.5 font-normal">Показателей в таблице</th>
            <th className="px-3 py-2.5 font-normal">Причина</th>
          </tr>
        </thead>
        <tbody>
          {coverage.works.map((work) => (
            <tr key={work.publication_id} className="border-b border-line align-top">
              <td className="px-3 py-2.5">{work.label}</td>
              <td className="px-3 py-2.5">
                {work.outcome ? (
                  <StatusPill tone={OUTCOME[work.outcome].tone}>
                    {OUTCOME[work.outcome].label}
                  </StatusPill>
                ) : (
                  <StatusPill tone="warning">Не обработана</StatusPill>
                )}
              </td>
              <td className="px-3 py-2.5">{work.measure_count}</td>
              <td className="px-3 py-2.5 text-ink-muted">{work.reason ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}
