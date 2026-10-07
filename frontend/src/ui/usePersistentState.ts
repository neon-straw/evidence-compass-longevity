import { useEffect, useState } from "react";

/**
 * Состояние экрана, которое переживает уход на другой шаг и возврат (shape §4.1):
 * фильтры, сортировка, поиск. Хранится в sessionStorage вкладки.
 */
export function usePersistentState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = sessionStorage.getItem(key);
      return stored === null ? initial : (JSON.parse(stored) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Недоступное хранилище не мешает работе экрана.
    }
  }, [key, value]);
  return [value, setValue] as const;
}

/** Восстанавливает прокрутку окна при возврате на экран, когда данные уже отрисованы. */
export function useScrollRestore(key: string, ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const stored = Number(sessionStorage.getItem(key) ?? 0);
    if (stored) window.scrollTo(0, stored);
    return () => sessionStorage.setItem(key, String(window.scrollY));
  }, [key, ready]);
}
