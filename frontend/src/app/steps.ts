export type Step = {
  path: string;
  label: string;
  /** Условие доступа к шагу; у первого шага его нет. */
  lockedReason?: string;
};

export const STEPS: readonly Step[] = [
  { path: "question", label: "Вопрос" },
  { path: "criteria", label: "Критерии", lockedReason: "Доступно после подготовки критериев" },
  { path: "search", label: "Поиск", lockedReason: "Доступно после подтверждения критериев" },
  { path: "selection", label: "Отбор", lockedReason: "Доступно после завершения поиска" },
  { path: "preparation", label: "Подготовка", lockedReason: "Доступно после выбора публикаций" },
  { path: "result", label: "Результат", lockedReason: "Доступно после подготовки" },
];
