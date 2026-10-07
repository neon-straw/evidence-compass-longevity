import { useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import { SESSION_KEY } from "./session";
import { STEPS } from "./steps";

/** Временная кнопка для отладки: удаляется вместе с `backend/.../api/reset.py`. */
export function ClearDataButton() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);

  async function clear() {
    if (!window.confirm("Удалить все данные исследования и начать заново?")) return;
    const ok = await api
      .DELETE("/api/study")
      .then(({ response }) => response.ok)
      .catch(() => false);
    setFailed(!ok);
    if (!ok) return;
    // Вход сохраняется; остальное перечитывается с сервера.
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== SESSION_KEY[0] });
    navigate(`/${STEPS[0].path}`, { replace: true });
  }

  return (
    <>
      {failed && (
        <span role="alert" className="text-caption text-warning-ink">
          Не удалось очистить данные. Повторите.
        </span>
      )}
      <button
        type="button"
        onClick={clear}
        className="flex items-center gap-1.5 text-ink-muted hover:text-ink"
      >
        <Trash2 aria-hidden className="size-4" />
        Очистить данные
      </button>
    </>
  );
}
