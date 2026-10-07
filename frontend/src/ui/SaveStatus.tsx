import { CircleAlert, CircleCheck, LoaderCircle } from "lucide-react";

export type SaveState = "idle" | "saving" | "saved" | "error";

/** Состояние автосохранения; цвет дополняет текст, но не заменяет его. */
export function SaveStatus({ state, savedText }: { state: SaveState; savedText: string }) {
  if (state === "idle") return null;
  if (state === "saving") {
    return (
      <span role="status" className="flex items-center gap-2 text-ink-muted">
        <LoaderCircle aria-hidden className="size-4 animate-spin" />
        Сохранение…
      </span>
    );
  }
  if (state === "error") {
    return (
      <span role="status" className="flex items-center gap-2 text-warning-ink">
        <CircleAlert aria-hidden className="size-4" />
        Не сохранено: сервер недоступен. Изменения остаются на экране.
      </span>
    );
  }
  return (
    <span role="status" className="flex items-center gap-2 text-success-ink">
      <CircleCheck aria-hidden className="size-5 fill-success text-surface" />
      {savedText}
    </span>
  );
}
