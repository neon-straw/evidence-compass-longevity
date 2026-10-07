import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleMinus, ExternalLink, Info, Pencil, X } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useRef } from "react";
import { api, isMockApi } from "../../api/client";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { Hint } from "../../ui/Hint";
import { KIND, ORIGIN, evidenceKey, publishedValue, rowLabel } from "./format";

type Row = Schemas["MeasureRow"];
type Evidence = Schemas["Evidence"];
const detailsGrid = "grid grid-cols-[14rem_minmax(0,1fr)] items-start gap-x-4 gap-y-2 text-caption";

/**
 * Нижняя панель основания выбранной строки (shape §3.6, §7): что подтверждается, нашлось ли
 * это в документе и где. Реальный фрагмент берётся из документа; если его нет, показана цитата
 * модели с явной пометкой — псевдофрагмент документа запрещён.
 */
export function EvidencePanel({
  row,
  headingRef,
  onCorrect,
  onClose,
  onDismiss,
  suspended,
}: {
  row: Row;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onCorrect: () => void;
  onClose: () => void;
  onDismiss: () => void;
  suspended: boolean;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const evidence = useQuery({
    queryKey: evidenceKey(row.id),
    queryFn: async () => {
      const { data } = await api.GET("/api/measures/{measure_id}/evidence", {
        params: { path: { measure_id: row.id } },
      });
      if (!data) throw new Error("Основание не загружено");
      return data;
    },
  });
  useEffect(() => {
    if (suspended) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    const outside = (event: MouseEvent) => {
      // Row clicks and explicit evidence buttons handle switching/toggling themselves.
      if (event.target instanceof Element) {
        const trigger = event.target.closest("[data-evidence-trigger]");
        const control = event.target.closest(
          "button, a, input, select, textarea, [role='menuitem']",
        );
        if (trigger && (!control || control.hasAttribute("data-evidence-trigger"))) return;
      }
      if (event.target instanceof Node && !panelRef.current?.contains(event.target)) onDismiss();
    };
    window.addEventListener("keydown", close);
    // Close before the outside control acts, without stealing its focus.
    window.addEventListener("click", outside, true);
    return () => {
      window.removeEventListener("keydown", close);
      window.removeEventListener("click", outside, true);
    };
  }, [onClose, onDismiss, suspended]);
  const value = evidence.data?.find((item) => item.role === "value");
  const context = evidence.data?.filter((item) => item.role === "context") ?? [];
  const [author, title] = splitLabel(row.publication_label);

  return (
    <section
      ref={panelRef}
      aria-labelledby="evidence-title"
      className="fixed inset-x-0 bottom-0 z-20 max-h-[42vh] overflow-y-auto rounded-t-panel border-t border-line bg-surface px-gutter pt-2 pb-4 shadow-[0_-8px_24px_rgb(0_0_0/0.08)]"
    >
      <div aria-hidden className="mx-auto mb-2 h-1 w-12 rounded-full bg-line" />
      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          <h2
            id="evidence-title"
            ref={headingRef}
            tabIndex={-1}
            className="text-xl font-semibold outline-none"
          >
            Основание · строка {rowLabel(row.number)}
          </h2>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <div className="flex items-center gap-3">
            {value && !isMockApi ? (
              <a
                href={documentUrl(value)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-control border border-line bg-surface px-4 py-2.5 font-medium text-ink hover:border-ink-muted"
              >
                <ExternalLink aria-hidden className="size-4" />
                {/* Открывается в браузере только PDF; XML и CSV скачиваются файлом. */}
                {value.locator?.kind === "pdf" ? "Открыть документ" : "Скачать документ"}
              </a>
            ) : (
              <Button
                aria-disabled="true"
                aria-describedby="document-view-note"
                className="cursor-not-allowed text-ink-muted"
                onClick={(event) => event.preventDefault()}
              >
                <ExternalLink aria-hidden className="size-4" />
                Открыть документ
              </Button>
            )}
            <Button variant="primary" onClick={onCorrect}>
              <Pencil aria-hidden className="size-4" />
              Исправить извлечение
            </Button>
            <Button onClick={onClose} aria-label="Закрыть основание">
              <X aria-hidden className="size-4" />
              Закрыть
            </Button>
          </div>
          {isMockApi && (
            <p id="document-view-note" className="text-caption text-ink-muted">
              В демо исходные документы недоступны.
            </p>
          )}
        </div>
      </div>

      <dl className={`mt-4 ${detailsGrid}`}>
        <Term hint="Вид показателя и значение, как они сохранены в строке результата. Среднее, медиана, наблюдаемый максимум и оценка возраста особи не взаимозаменяемы.">
          Показатель
        </Term>
        <dd>
          <span className="-mx-2 rounded-control bg-accent-soft px-2 py-0.5 font-semibold">
            {KIND[row.measure_kind]} — {publishedValue(row)}
          </span>
        </dd>
        <Term hint="Как авторы сами описали этот показатель в публикации, без перевода и обобщения.">
          Определение в источнике
        </Term>
        <dd>{row.definition}</dd>
        <Term hint="Собственный результат этой работы или значение, которое авторы приводят по другим работам или как известный факт, например во введении. Ссылки рядом может не быть.">
          Происхождение
        </Term>
        <dd>
          {ORIGIN[row.origin_kind].label}
          {ORIGIN[row.origin_kind].detail && (
            <span className="text-ink-muted"> — {ORIGIN[row.origin_kind].detail}</span>
          )}
        </dd>
        {value && (
          <>
            <Term hint="Документ публикации и его версия, из которых извлечено значение: например, основной текст или приложение.">
              Документ
            </Term>
            <dd>
              {value.document_name}, версия {value.document_version}
            </dd>
            <Term hint="Где в документе стоит цитата со значением: страница, узел текста или строка таблицы. Если место не найдено, сверьте значение по цитате.">
              Место в документе
            </Term>
            <dd>{place(value)}</dd>
          </>
        )}
      </dl>

      {evidence.isPending && (
        <p role="status" className="mt-3 text-ink-muted">
          Загружаем основание…
        </p>
      )}
      {evidence.isError && (
        <p role="alert" className="mt-3">
          Не удалось загрузить основание: сервер недоступен.
        </p>
      )}
      {evidence.data && !value && (
        <Callout tone="warning">
          Модель не указала, где в документе это значение: сверьте строку с документом.
        </Callout>
      )}
      {value && (
        <div className="mt-3 grid grid-cols-[1fr_20rem] gap-6">
          <div className="space-y-3">
            <Status evidence={value} />
            <Source evidence={value} />
            <dl className={detailsGrid}>
              {row.method && (
                <>
                  <Term hint="Как авторы получили или оценили этот показатель: например, наблюдение за животными или оценка возраста по образцам. Это извлечённое описание, а не оценка качества метода.">
                    Метод
                  </Term>
                  <dd>{row.method}</dd>
                </>
              )}
              {context.length > 0 && (
                <>
                  <Term hint="Цитаты, сохранённые при извлечении сведений о методе и условиях, с указанием места в документе. Цитата может относиться к одному из этих полей; её наличие не означает, что оба поля подтверждены.">
                    Цитаты о методе и условиях
                  </Term>
                  <dd>
                    <ul className="space-y-2">
                      {context.map((item) => (
                        <li key={item.id}>
                          <span className="text-ink-muted">{place(item)}</span>
                          {item.quote && `: «${item.quote}»`}
                        </li>
                      ))}
                    </ul>
                  </dd>
                </>
              )}
              {row.taxonomy_note && (
                <>
                  <dt className="text-ink-muted">Таксономия</dt>
                  <dd>{row.taxonomy_note}</dd>
                </>
              )}
              <Term hint="Ограничения исследования или результата, прямо указанные авторами публикации. Это не самостоятельная оценка системы. Если сведения не извлечены, это не означает, что ограничений нет.">
                Ограничения авторов
              </Term>
              <dd>
                {row.author_notes ??
                  "Ограничения, прямо указанные авторами, не извлечены. Это не означает, что ограничений нет."}
              </dd>
            </dl>
          </div>
          <dl className="grid h-fit grid-cols-[7rem_1fr] gap-x-4 gap-y-1 text-caption">
            <dt className="text-ink-muted">Публикация</dt>
            <dd>
              {author}
              {title && <span className="block text-ink-muted">{title}</span>}
            </dd>
            <dt className="text-ink-muted">Идентификатор</dt>
            <dd className="break-all">{value.source.identifier}</dd>
            <dt className="text-ink-muted">Тип</dt>
            <dd>{value.source.source_type}</dd>
            <dt className="text-ink-muted">Найдена в</dt>
            <dd>{value.source.resource}</dd>
          </dl>
        </div>
      )}
      <p className="mt-3 flex items-center gap-2 text-caption text-ink-muted">
        <Info aria-hidden className="size-4" />
        Проверка извлечения не равна научному подтверждению.
      </p>
    </section>
  );
}

/** Подпись поля основания с подсказкой по наведению или фокусу. */
function Term({ hint, children }: { hint: string; children: ReactNode }) {
  return (
    <dt className="text-ink-muted">
      <Hint text={hint}>
        <button
          type="button"
          className="cursor-help text-left underline decoration-dotted underline-offset-4"
        >
          {children}
        </button>
      </Hint>
    </dt>
  );
}

/** «Автор 2026 · Название» → автор с годом и название отдельно. */
function splitLabel(label: string): [string, string | null] {
  const [author, ...rest] = label.split(" · ");
  return [author, rest.join(" · ") || null];
}

const place = (evidence: Evidence) => evidence.locator?.printed ?? "место в документе не найдено";

/** Файл именно этой версии документа; PDF открывается на установленной странице. */
function documentUrl(evidence: Evidence): string {
  const url = `/api/documents/${evidence.document_id}/versions/${evidence.document_version}/file`;
  return evidence.locator?.page ? `${url}#page=${evidence.locator.page}` : url;
}

const CALLOUT = {
  success: { box: "bg-success-soft text-success-ink", icon: CircleCheck },
  warning: { box: "bg-warning-soft text-warning-ink", icon: CircleAlert },
  neutral: { box: "bg-neutral-soft text-ink", icon: CircleMinus },
} as const;

function Callout({ tone, children }: { tone: keyof typeof CALLOUT; children: ReactNode }) {
  const { box, icon: Icon } = CALLOUT[tone];
  return (
    <p className={`mt-3 flex items-start gap-2 rounded-control px-3 py-2 first:mt-0 ${box}`}>
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

/**
 * Итог автоматической сверки цитаты с документом одной фразой. Это техническая проверка
 * извлечения, не научная; для значения сверяются число и единицы.
 */
function Status({ evidence }: { evidence: Evidence }) {
  const note = evidence.diagnostic ? ` ${evidence.diagnostic}` : "";
  switch (evidence.verification_status) {
    case "verified":
      return (
        <Callout tone="success">
          Цитата найдена в документе, число и единицы в ней совпадают со строкой.{note}
        </Callout>
      );
    case "needs_review":
      return evidence.locator ? (
        <Callout tone="warning">
          Цитата найдена в документе, но значение строки ею не подтверждено.{note}
        </Callout>
      ) : (
        <Callout tone="warning">
          Цитата модели не найдена в документе: найдите место по цитате ниже и сверьте значение.
          {evidence.diagnostic && evidence.diagnostic !== NOT_FOUND ? note : ""}
        </Callout>
      );
    case "pending":
      return (
        <Callout tone="neutral">
          Строка извлечена до автоматической сверки с документом: сверьте значение по цитате ниже.
        </Callout>
      );
    case "rejected":
      return <Callout tone="neutral">Основание отклонено.{note}</Callout>;
  }
}

const NOT_FOUND = "Цитата не найдена в документе.";

/**
 * Реальный фрагмент документа или, если его нет, цитата модели с явной пометкой: это слова из
 * ответа модели, а не проверенный текст документа.
 */
function Source({ evidence }: { evidence: Evidence }) {
  if (evidence.fragment) {
    return (
      <figure>
        <figcaption className="mb-1 text-caption text-ink-muted">
          {isMockApi ? "Демо-фрагмент" : "Фрагмент документа"}
        </figcaption>
        <pre className="overflow-x-auto rounded-control border border-line bg-canvas py-2 text-caption whitespace-pre-wrap">
          {evidence.fragment.map((line) => (
            <div
              key={line.number}
              className={`flex gap-4 px-3 ${line.highlighted ? "bg-accent-soft" : ""}`}
            >
              <span className="w-8 shrink-0 text-right text-ink-muted">{line.number}</span>
              <span>
                {line.highlighted && <span className="sr-only">Основание: </span>}
                {line.text}
              </span>
            </div>
          ))}
        </pre>
      </figure>
    );
  }
  if (!evidence.quote) {
    return <p className="text-caption text-ink-muted">{evidence.precision_note}</p>;
  }
  return (
    <figure>
      <figcaption className="mb-1 text-caption text-ink-muted">
        Цитата из ответа модели — не фрагмент документа · модель указала место: «
        {evidence.claimed_location}»
      </figcaption>
      <blockquote className="rounded-control border border-dashed border-line px-3 py-2 italic">
        {evidence.quote}
      </blockquote>
      {evidence.locator && evidence.precision_note && (
        <p className="mt-1 text-caption text-ink-muted">{evidence.precision_note}</p>
      )}
    </figure>
  );
}
