import { useCallback, useEffect, useRef, useState } from "react";
import type { SaveState } from "./SaveStatus";

/**
 * Сохраняет после паузы во вводе. Сохранения идут строго по очереди, поэтому поздний ответ не
 * перекрывает более новый. Неудачное значение остаётся в очереди: следующий `flush` повторит его,
 * а не сообщит об успехе. `flush` сохраняет немедленно — перед действием, которому нужна последняя
 * версия, и при уходе со страницы.
 */
export function useAutosave<T>(
  save: (value: T) => Promise<boolean>,
  { initiallySaved = false, delayMs = 600 } = {},
) {
  const [state, setState] = useState<SaveState>(initiallySaved ? "saved" : "idle");
  const saveRef = useRef(save);
  saveRef.current = save;
  const pending = useRef<{ value: T } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const queue = useRef<Promise<boolean>>(Promise.resolve(true));

  const flush = useCallback((): Promise<boolean> => {
    clearTimeout(timer.current);
    queue.current = queue.current.then(async () => {
      const next = pending.current;
      if (!next) return true;
      pending.current = null;
      const ok = await saveRef.current(next.value).catch(() => false);
      if (!ok) pending.current ??= next;
      // Пока ждёт более новая правка, итог этого сохранения не показываем.
      if (ok ? pending.current === null : pending.current === next) {
        setState(ok ? "saved" : "error");
      }
      return ok;
    });
    return queue.current;
  }, []);

  const schedule = useCallback(
    (value: T) => {
      pending.current = { value };
      setState("saving");
      clearTimeout(timer.current);
      timer.current = setTimeout(flush, delayMs);
    },
    [flush, delayMs],
  );

  useEffect(() => () => void flush(), [flush]);

  return { state, schedule, flush };
}
