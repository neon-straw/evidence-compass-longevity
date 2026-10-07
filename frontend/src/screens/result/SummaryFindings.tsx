import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { plural } from "../../ui/plural";
import { StatusPill, type Tone } from "../../ui/StatusPill";
import { rowLabel } from "./format";

type Finding = Schemas["Finding"];
type SummaryWork = Schemas["SummaryWork"];

/** Итог обработки работы, показанный рядом с её именем в списке без показателей. */
const WORK_OUTCOME: Record<NonNullable<SummaryWork["outcome"]>, { tone: Tone; label: string }> = {
  measures_extracted: { tone: "success", label: "Показатели извлечены" },
  no_measures: { tone: "neutral", label: "Показателей нет" },
  unresolved: { tone: "warning", label: "Требует решения" },
  unavailable: { tone: "neutral", label: "Документ недоступен" },
  error: { tone: "warning", label: "Ошибка обработки" },
};
const NOT_PROCESSED_OUTCOME = { tone: "neutral" as Tone, label: "Не обработана" };

const SET_ASIDE_KIND: Record<Schemas["SetAsideItem"]["kind"], string> = {
  simulation: "симуляция",
  raw_data: "исходные данные",
  figure_only: "только на графике",
  other_species: "другой вид",
  other: "другое",
};

/** Сервер отдаёт фразы без точки; вопрос или многоточие источника не превращаются в «?.». */
const sentence = (text: string) => (/[.!?…]$/.test(text) ? text : `${text}.`);

/** «Работы без показателей в выводе»: у каждой работы — отдельная строка с её причинами,
 * а не одна склеенная фраза (issue #42). Работы, требующие решения, идут первыми (сервер). */
export function AbsentWorks({
  works,
  onOpenPublication,
}: {
  works: SummaryWork[];
  onOpenPublication: (publicationId: string, trigger: HTMLElement) => void;
}) {
  if (works.length === 0) return null;
  return (
    <div className="mt-3 text-caption">
      <h4 className="font-medium">Работы без показателей в выводе · {works.length}</h4>
      <ul className="mt-2 max-w-4xl space-y-3">
        {works.map((work) => {
          const status = work.outcome ? WORK_OUTCOME[work.outcome] : NOT_PROCESSED_OUTCOME;
          return (
            <li key={work.publication_id}>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  aria-label={`Карточка публикации ${work.label}`}
                  className="font-medium text-link hover:underline"
                  onClick={(e) => onOpenPublication(work.publication_id, e.currentTarget)}
                >
                  {work.label}
                </button>
                <StatusPill tone={status.tone}>{status.label}</StatusPill>
              </div>
              {/* Причины сдвинуты под имя работы: видно, к какой работе они относятся. */}
              <div className="pl-4">
                {/* Расхождение требует действия пользователя — основным цветом, пробелы — справка. */}
                {work.unresolved.map((text, i) => (
                  <p key={`u${i}`} className="mt-1 text-ink">
                    {sentence(text)}
                  </p>
                ))}
                {work.gaps.map((text, i) => (
                  <p key={`g${i}`} className="mt-1 text-ink-muted">
                    {sentence(text)}
                  </p>
                ))}
                {work.set_aside.length > 0 && (
                  <details className="mt-1 text-ink-muted">
                    <summary className="cursor-pointer hover:underline">
                      Не стали показателями:{" "}
                      {plural(work.set_aside.length, ["запись", "записи", "записей"])}
                    </summary>
                    <ul className="mt-1 space-y-1 pl-4">
                      {work.set_aside.map((item, i) => (
                        <li key={i}>
                          {SET_ASIDE_KIND[item.kind]}: {sentence(item.description)}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function SummaryFindings({
  findings,
  onSelect,
}: {
  findings: Finding[];
  onSelect: (id: string) => void;
}) {
  return (
    <div className="mt-3 space-y-5">
      {findings.map((finding) => (
        <div key={finding.measure_refs.join(",")}>
          {finding.items?.length ? (
            <>
              <h3 className="font-semibold">{finding.species}</h3>
              <FindingList items={finding.items.slice(0, 3)} onSelect={onSelect} />
              {finding.items.length > 3 && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-accent hover:underline">
                    Ещё показателей: {finding.items.length - 3}
                  </summary>
                  <FindingList items={finding.items.slice(3)} onSelect={onSelect} />
                </details>
              )}
            </>
          ) : (
            <p className="text-lg">
              {finding.text}{" "}
              <Button
                data-evidence-trigger
                variant="link"
                onClick={() => onSelect(finding.measure_refs[0])}
              >
                К строкам
              </Button>
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function FindingList({
  items,
  onSelect,
}: {
  items: Schemas["FindingItem"][];
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="mt-2 space-y-4">
      {items.map((item) => (
        <li key={item.measure_id}>
          <p className="text-lg font-medium">{item.title}</p>
          <p className="mt-0.5 text-ink-muted">{item.context}</p>
          <p className="mt-1 text-caption text-ink-muted">
            {item.publication} ·{" "}
            <Button data-evidence-trigger variant="link" onClick={() => onSelect(item.measure_id)}>
              Строка {rowLabel(item.number)}
            </Button>
            {item.checked_manually && " · сверено вручную"}
          </p>
        </li>
      ))}
    </ul>
  );
}
