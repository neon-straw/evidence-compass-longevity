import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { STEPS } from "./steps";

export const STUDY_KEY = ["study"] as const;

/** Индекс последнего достигнутого шага; `null`, пока прогресс исследования загружается. */
export function useReachedStep(): number | null {
  const study = useQuery({
    queryKey: STUDY_KEY,
    queryFn: async () => (await api.GET("/api/study")).data ?? null,
  });
  if (study.isPending) return null;
  const reached = study.data?.reached_step;
  return Math.max(
    0,
    STEPS.findIndex((step) => step.path === reached),
  );
}
