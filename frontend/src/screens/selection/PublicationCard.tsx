import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";
import { type ReactNode, type RefObject, useState } from "react";
import { api, isMockApi } from "../../api/client";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";
import { StatusPill } from "../../ui/StatusPill";
import { publicationKey, useSelectionActions } from "./actions";
import { DEFAULT_USER_REASON, DecisionControl } from "./DecisionControl";
import { DECISIONS, OPEN_ACCESS, VERSION_TYPE, provenanceLabel } from "./labels";

type Card = Schemas["PublicationCard"];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-line py-4">
      <h3 className="text-lg font-semibold">{title}</h3>
      <dl className="mt-2 grid grid-cols-[9rem_1fr] gap-x-4 gap-y-1.5">{children}</dl>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-muted">{label}:</dt>
      <dd>{children}</dd>
    </>
  );
}

/**
 * Правая выдвижная панель карточки. Закрытие возвращает фокус к иконке строки,
 * а если строка скрыта фильтром — к `fallbackFocus`.
 */
export function PublicationCard({
  publicationId,
  rowNumber,
  readOnly = false,
  evidenceMeasureId = null,
  returnFocus,
  fallbackFocus,
  onClose,
}: {
  publicationId: string | null;
  // Номер строки таблицы отбора; null — карточка открыта с другого экрана.
  rowNumber: number | null;
  // Решение об отборе меняется только на шаге «Отбор».
  readOnly?: boolean;
  evidenceMeasureId?: string | null;
  returnFocus: RefObject<HTMLElement | null>;
  fallbackFocus: RefObject<HTMLElement | null>;
  onClose: () => void;
}) {
  const card = useQuery({
    queryKey: publicationKey(publicationId ?? ""),
    enabled: publicationId !== null,
    queryFn: async () => {
      const { data } = await api.GET("/api/publications/{publication_id}", {
        params: { path: { publication_id: publicationId! } },
      });
      if (!data) throw new Error("Карточка не загружена");
      return data;
    },
  });
  const row =
    rowNumber === null
      ? `${isMockApi ? "Демо · " : ""}решение об отборе меняется на шаге «Отбор»`
      : `${isMockApi ? "Демо · строка" : "Строка"} ${String(rowNumber).padStart(2, "0")}`;

  return (
    <Dialog.Root modal={false} open={publicationId !== null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Content
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocus.current?.isConnected
              ? returnFocus.current
              : fallbackFocus.current;
            target?.focus();
          }}
          onInteractOutside={(event) => event.preventDefault()}
          className="fixed top-0 right-0 z-40 flex h-full w-[38rem] max-w-[90vw] flex-col border-l border-line bg-surface shadow-xl"
        >
          <header className="flex items-start justify-between border-b border-line px-6 py-4">
            <div>
              <Dialog.Title className="text-xl font-semibold">Карточка публикации</Dialog.Title>
              <Dialog.Description className="text-caption text-ink-muted">{row}</Dialog.Description>
            </div>
            <Dialog.Close
              aria-label="Закрыть карточку"
              className="p-1 text-ink-muted hover:text-ink"
            >
              <X aria-hidden className="size-5" />
            </Dialog.Close>
          </header>
          <div className="flex-1 overflow-y-auto px-6 pb-4">
            {card.isPending && (
              <p role="status" className="mt-4 text-ink-muted">
                Загружаем карточку…
              </p>
            )}
            {card.isError && (
              <p role="alert" className="mt-4">
                Не удалось загрузить карточку: сервер недоступен.
              </p>
            )}
            {card.data && (
              <CardBody
                key={card.data.id}
                card={card.data}
                readOnly={readOnly}
                evidenceMeasureId={evidenceMeasureId}
              />
            )}
          </div>
          <footer className="flex justify-end border-t border-line px-6 py-4">
            <Dialog.Close asChild>
              <Button>Закрыть карточку</Button>
            </Dialog.Close>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function CardBody({
  card,
  readOnly,
  evidenceMeasureId,
}: {
  card: Card;
  readOnly: boolean;
  evidenceMeasureId: string | null;
}) {
  const actions = useSelectionActions();
  const record = card.decision_record;
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editingReason, setEditingReason] = useState(false);
  const [reason, setReason] = useState(record.reason);

  async function run(action: () => Promise<string | null>) {
    setBusy(true);
    setMessage(await action());
    setBusy(false);
  }

  return (
    <>
      <h2 className="py-4 text-2xl leading-snug font-semibold">{card.title}</h2>
      {message && (
        <p role="alert" className="mb-3 rounded-control bg-warning-soft px-3 py-2">
          {message}
        </p>
      )}

      <Section title="Метаданные">
        <Field label="Год">{card.year ?? "не указан"}</Field>
        <Field label="Тип">{VERSION_TYPE[card.version_type]}</Field>
        <Field label="Авторы">{card.authors || "не указаны"}</Field>
        <Field label="DOI">{card.doi ?? "не указан"}</Field>
      </Section>

      <Section title="Происхождение">
        <Field label="Источник">{provenanceLabel(card)}</Field>
        {card.sources.map((source) => (
          <Field key={source.identifier} label="Идентификатор">
            {source.url ? (
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-accent hover:text-accent-strong"
              >
                {source.identifier}
                <ExternalLink aria-hidden className="size-4" />
                <span className="sr-only">(открывается в новой вкладке)</span>
              </a>
            ) : (
              source.identifier
            )}
          </Field>
        ))}
      </Section>

      <section className="border-t border-line py-4">
        <h3 className="text-lg font-semibold">Аннотация</h3>
        <p className="mt-2 text-ink-muted">{card.abstract ?? "Аннотация не получена."}</p>
      </section>

      <Section title="Соответствие критериям">
        <Field label="Соответствует">
          {readOnly ? (
            <StatusPill tone={DECISIONS[card.decision].tone}>
              {DECISIONS[card.decision].label}
            </StatusPill>
          ) : (
            <DecisionControl
              title={card.title}
              decision={card.decision}
              onChange={(decision) =>
                run(() => actions.changeDecision(card.id, decision, DEFAULT_USER_REASON))
              }
            />
          )}
        </Field>
        <Field label="Обоснование">
          {editingReason ? (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run(async () => {
                  const failure = await actions.changeDecision(
                    card.id,
                    card.decision,
                    reason.trim(),
                  );
                  if (!failure) setEditingReason(false);
                  return failure;
                });
              }}
            >
              <textarea
                aria-label="Обоснование решения"
                rows={3}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                className="w-full rounded-control border border-line px-2 py-1 outline-none focus:border-accent"
              />
              <div className="mt-1 flex gap-3">
                <Button type="submit" variant="link" disabled={busy || reason.trim() === ""}>
                  Сохранить обоснование
                </Button>
                <Button variant="link" onClick={() => setEditingReason(false)}>
                  Отмена
                </Button>
              </div>
            </form>
          ) : (
            <>
              {record.reason}{" "}
              {!readOnly && (
                <Button
                  variant="link"
                  onClick={() => {
                    setReason(record.reason);
                    setEditingReason(true);
                  }}
                >
                  Уточнить
                </Button>
              )}
            </>
          )}
        </Field>
        <Field label="Автор решения">
          {record.author === "user" ? "пользователь" : "система"}, версия критериев{" "}
          {record.criteria_version}
        </Field>
      </Section>

      <Section title="Выбор для извлечения">
        <Field label="В извлечение">
          {card.selected ? "выбрана" : "не выбрана"}
          {!card.selected && card.select_blocked_reason && (
            <p className="text-caption text-ink-muted">{card.select_blocked_reason}</p>
          )}
        </Field>
      </Section>

      <Section title="Версии">
        <Field label="Доступные версии">
          {card.related_versions.length === 0 ? (
            <>
              1 · {VERSION_TYPE[card.version_type].toLowerCase()}
              <p className="text-caption text-ink-muted italic">Связанные версии не найдены.</p>
            </>
          ) : (
            card.related_versions.map((related) => (
              <div key={related.id}>
                <p>
                  {VERSION_TYPE[related.version_type]}: {related.title}
                </p>
                {related.link === "confirmed" ? (
                  <p className="text-caption text-success-ink">
                    Связь версий подтверждена: это одна работа.
                  </p>
                ) : (
                  <>
                    <p className="text-caption text-warning-ink">
                      Возможно, другая версия этой работы. Сходство названий не подтверждает связь.
                    </p>
                    <div className="mt-1 flex gap-4">
                      <Button
                        variant="link"
                        disabled={busy}
                        onClick={() =>
                          run(() => actions.resolveVersionLink(card.id, related.id, true))
                        }
                      >
                        Это одна работа
                      </Button>
                      <Button
                        variant="link"
                        disabled={busy}
                        onClick={() =>
                          run(() => actions.resolveVersionLink(card.id, related.id, false))
                        }
                      >
                        Это разные работы
                      </Button>
                    </div>
                  </>
                )}
              </div>
            ))
          )}
        </Field>
      </Section>

      <Section title="Полный текст">
        <Field label="Открытый текст">
          <StatusPill tone={OPEN_ACCESS[card.open_access].tone}>
            {OPEN_ACCESS[card.open_access].label}
          </StatusPill>
          <p className="mt-1 text-caption text-ink-muted">
            По сведениям Europe PMC, без загрузки документа.
          </p>
        </Field>
        <Field label="Действие">
          {evidenceMeasureId ? (
            <EvidenceDocuments measureId={evidenceMeasureId} />
          ) : (
            <>
              <Button disabled aria-describedby={`document-note-${card.id}`}>
                <ExternalLink aria-hidden className="size-4" />
                Открыть документ
              </Button>
              <p id={`document-note-${card.id}`} className="mt-1 text-caption text-ink-muted">
                Документы загружаются на шаге «Подготовка»; там же их можно открыть.
              </p>
            </>
          )}
        </Field>
      </Section>
    </>
  );
}

/** Open the immutable document versions behind the result, not the latest upload. */
function EvidenceDocuments({ measureId }: { measureId: string }) {
  const evidence = useQuery({
    queryKey: ["evidence", measureId],
    queryFn: async () => {
      const { data } = await api.GET("/api/measures/{measure_id}/evidence", {
        params: { path: { measure_id: measureId } },
      });
      if (!data) throw new Error("Основание не загружено");
      return data;
    },
  });
  if (evidence.isPending) return <p role="status">Загружаем документы…</p>;
  if (evidence.isError)
    return (
      <p role="alert">
        Не удалось загрузить документы.{" "}
        <Button variant="link" onClick={() => evidence.refetch()}>
          Повторить
        </Button>
      </p>
    );
  const documents = [
    ...new Map(evidence.data.map((item) => [item.document_version_id, item])).values(),
  ];
  if (!documents.length)
    return (
      <p className="text-ink-muted">
        Для показателя не сохранено основание со ссылкой на документ.
      </p>
    );
  return (
    <div>
      <p className="mb-2 text-caption text-ink-muted">Документы основания выбранного показателя</p>
      <ul className="space-y-3">
        {documents.map((document) => (
          <li key={document.document_version_id}>
            <p className="mb-1 text-caption">
              {document.document_name}, версия {document.document_version}
            </p>
            {isMockApi ? (
              <Button disabled>Открыть документ</Button>
            ) : (
              <a
                href={`/api/documents/${document.document_id}/versions/${document.document_version}/file`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-control border border-line px-4 py-2.5 font-medium hover:border-ink-muted"
              >
                <ExternalLink aria-hidden className="size-4" />
                Открыть документ
              </a>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-caption text-ink-muted">
        {isMockApi
          ? "В демо исходные документы недоступны."
          : "PDF откроется в новой вкладке; другие форматы скачаются файлом."}
      </p>
    </div>
  );
}
