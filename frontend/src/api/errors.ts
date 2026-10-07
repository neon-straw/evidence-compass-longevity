/** Текст отказа сервера (Problem.message или detail), если он есть; иначе — недоступность. */
export function failureReason(error: unknown): string {
  if (error && typeof error === "object") {
    if ("message" in error && typeof error.message === "string") return error.message;
    if ("detail" in error && typeof error.detail === "string") return error.detail;
  }
  return "сервер недоступен";
}
