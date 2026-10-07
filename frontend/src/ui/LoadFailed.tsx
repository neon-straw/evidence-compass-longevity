import { Button } from "./Button";
import { Panel } from "./Page";

/** Экран не загрузился: называем, что именно, и даём безопасное действие. */
export function LoadFailed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <Panel className="px-8 py-7">
      <p role="alert">
        Не удалось загрузить {what}: сервер недоступен. Сохранённые данные не изменены.
      </p>
      <Button className="mt-4" onClick={onRetry}>
        Повторить
      </Button>
    </Panel>
  );
}
