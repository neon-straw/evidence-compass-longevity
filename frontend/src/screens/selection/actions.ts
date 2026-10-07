import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { failureReason } from "../../api/errors";
import type { Decision } from "../../api/types";

export const SELECTION_KEY = ["selection"] as const;
export const publicationKey = (id: string) => ["publication", id] as const;

/**
 * Изменения отбора; после каждого обновляются таблица и карточка.
 * Каждое действие возвращает null при успехе или текст причины отказа.
 */
export function useSelectionActions() {
  const queryClient = useQueryClient();
  const refresh = (...ids: string[]) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: SELECTION_KEY }),
      ...ids.map((id) => queryClient.invalidateQueries({ queryKey: publicationKey(id) })),
    ]);

  async function attempt(
    request: () => Promise<{ data?: unknown; error?: unknown }>,
    failure: string,
    ...ids: string[]
  ): Promise<string | null> {
    try {
      const { data, error } = await request();
      return data ? null : `${failure}: ${failureReason(error)}.`;
    } catch {
      return `${failure}: сервер недоступен.`;
    } finally {
      await refresh(...ids);
    }
  }

  return {
    changeDecision: (id: string, decision: Decision, why: string) =>
      attempt(
        () =>
          api.PUT("/api/publications/{publication_id}/decision", {
            params: { path: { publication_id: id } },
            body: { decision, reason: why },
          }),
        "Решение не сохранено",
        id,
      ),
    changeSelected: (id: string, selected: boolean) =>
      attempt(
        () =>
          api.PUT("/api/publications/{publication_id}/selected", {
            params: { path: { publication_id: id } },
            body: { selected },
          }),
        "Выбор не сохранён",
        id,
      ),
    resolveVersionLink: (id: string, relatedId: string, sameWork: boolean) =>
      attempt(
        () =>
          api.POST("/api/publications/{publication_id}/version-links", {
            params: { path: { publication_id: id } },
            body: { related_id: relatedId, same_work: sameWork },
          }),
        "Связь версий не сохранена",
        id,
        relatedId,
      ),
    refresh,
  };
}
