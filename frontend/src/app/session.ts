import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export const SESSION_KEY = ["session"] as const;

/** Текущий вход: логин, `null` — вход не выполнен; сбой сервера — ошибка запроса. */
export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/session");
      if (data) return data;
      if (response.status === 401) return null;
      throw new Error(`Проверка входа: HTTP ${response.status}`);
    },
  });
}

/** «a.chernov» → «AC», «researcher» → «R». */
export function initials(login: string): string {
  return login
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}
