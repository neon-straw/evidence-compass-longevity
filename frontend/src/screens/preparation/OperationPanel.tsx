import { CircleAlert, CircleCheck, CircleMinus, CirclePause, Hourglass } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { Link } from "react-router";
import type { Schemas } from "../../api/types";
import { Button } from "../../ui/Button";

type Op = Schemas["Operation"];
function summary(op: Op): { icon: ReactNode; title: string; text: string } {
  const failed = op.works.filter((w) => w.execution === "failed").length;
  const withMeasures = op.works.filter((w) => w.outcome === "measures_extracted").length;
  const incomplete = op.works.some(
    (w) => w.execution !== "done" || w.outcome !== "measures_extracted" || w.gaps.length > 0,
  );
  const warn = <CircleAlert aria-hidden className="size-10 fill-warning text-surface" />;
  const muted = <CircleMinus aria-hidden className="size-10 fill-ink-muted text-surface" />;
  const pause = <CirclePause aria-hidden className="size-10 text-accent" />;
  switch (op.status) {
    case "queued":
      return {
        icon: <Hourglass aria-hidden className="size-10 text-accent" />,
        title: "Извлечение в очереди",
        text: "Операция начнётся, как только освободится обработка.",
      };
    case "running":
      return {
        icon: null,
        title: "Извлечение выполняется",
        text: "Можно закрыть браузер — обработка продолжится. Готовые результаты сохраняются по мере работы.",
      };
    case "waiting_llm":
      return {
        icon: pause,
        title: "Ожидание модели",
        text: "Модель не ответила в пределах повторов. Продолжите явно — модель не меняется автоматически.",
      };
    case "pause_requested":
      return {
        icon: pause,
        title: "Пауза запрошена",
        text: "Текущая работа завершается; следующие не начнутся, пока вы не продолжите.",
      };
    case "paused":
      return {
        icon: pause,
        title: "Извлечение на паузе",
        text: "Пауза подтверждена. Готовые результаты сохранены; документы можно менять только после остановки.",
      };
    case "terminate_requested":
      return {
        icon: muted,
        title: "Остановка запрошена",
        text: "Текущая работа завершается; оставшиеся работы не будут обработаны.",
      };
    case "terminated":
      return {
        icon: muted,
        title: "Извлечение остановлено",
        text: "Готовые результаты сохранены. Необработанные работы можно запустить снова.",
      };
    case "failed":
      return {
        icon: warn,
        title: "Сбой операции",
        text: "Последняя успешная ревизия результата доступна. Повторите неудачные работы.",
      };
    default:
      if (withMeasures === 0) {
        return {
          icon: muted,
          title: "Извлечение завершено без показателей",
          text: "Результат покажет охват и причины, но не содержательный вывод.",
        };
      }
      if (failed > 0 || incomplete) {
        return {
          icon: warn,
          title: "Извлечение завершено частично",
          text: "Готовые результаты сохранены; неудачные и необработанные работы можно запустить снова, не затрагивая готовое.",
        };
      }
      return {
        icon: <CircleCheck aria-hidden className="size-10 fill-success text-surface" />,
        title: "Извлечение завершено",
        text: "Результаты всех работ сохранены.",
      };
  }
}

/** Ход общей операции над таблицей документов; итог каждой работы — в её строке (shape §3.5). */
export function OperationPanel({
  op,
  titleRef,
  onPause,
  onResume,
  onStop,
  onRetryFailed,
}: {
  op: Op;
  titleRef: RefObject<HTMLParagraphElement | null>;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onRetryFailed: () => void;
}) {
  const { icon, title, text } = summary(op);
  const allowed = (action: Op["allowed_actions"][number]) => op.allowed_actions.includes(action);
  const inFlight = ["queued", "running", "pause_requested", "terminate_requested"].includes(
    op.status,
  );

  return (
    <section aria-label="Ход извлечения" className="border-t border-line px-6 py-4">
      <div className="flex items-center gap-4">
        {icon}
        <div role="status" className="min-w-64">
          <p ref={titleRef} tabIndex={-1} className="text-xl font-semibold outline-none">
            {title}
          </p>
          <p className="mt-1 text-ink-muted">{text}</p>
          {["completed", "terminated", "failed"].includes(op.status) && (
            <p className="mt-1 text-caption text-ink-muted">
              Завершение обработки не означает научного подтверждения результатов.
            </p>
          )}
        </div>
        {inFlight && (
          <div
            role="progressbar"
            aria-label="Ход извлечения"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(op.progress * 100)}
            className="mx-6 h-2.5 flex-1 overflow-hidden rounded-full bg-neutral-soft"
          >
            <div
              className="h-full rounded-full bg-accent/70 transition-[width]"
              style={{ width: `${Math.max(6, op.progress * 100)}%` }}
            />
          </div>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-3">
          <Link to="/preparation/log" className="text-accent hover:text-accent-strong">
            Журнал операции
          </Link>
          {allowed("retry_failed") && <Button onClick={onRetryFailed}>Повторить неудачные</Button>}
          {allowed("pause") && <Button onClick={onPause}>Приостановить</Button>}
          {allowed("resume") && (
            <Button variant="primary" onClick={onResume}>
              Продолжить
            </Button>
          )}
          {allowed("stop") && <Button onClick={onStop}>Остановить</Button>}
        </div>
      </div>
    </section>
  );
}
