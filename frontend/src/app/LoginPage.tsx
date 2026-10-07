import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router";
import { api } from "../api/client";
import { SESSION_KEY, useSession } from "./session";

const field =
  "w-full rounded-control border border-line bg-surface px-3 py-2 outline-none focus:border-accent focus:ring-2 focus:ring-accent-soft";

export function LoginPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (session.data) return <Navigate to="/" replace />;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    const unavailable = "Не удалось войти: сервер недоступен. Повторите позже.";
    try {
      const { data, response } = await api.POST("/api/session", {
        body: { login: String(form.get("login")), password: String(form.get("password")) },
      });
      if (!data) {
        setError(response.status === 401 ? "Неверный логин или пароль." : unavailable);
        return;
      }
      queryClient.setQueryData(SESSION_KEY, data);
      navigate("/", { replace: true });
    } catch {
      setError(unavailable);
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="grid min-h-[calc(100vh-4rem)] place-items-center p-gutter">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-panel border border-line bg-surface px-6 py-6"
      >
        <div>
          <p className="text-lg font-semibold">Evidence Compass</p>
          <h1 className="text-ink-muted">Вход в исследование</h1>
        </div>
        <label className="block space-y-1">
          <span>Логин</span>
          <input name="login" autoComplete="username" required className={field} />
        </label>
        <label className="block space-y-1">
          <span>Пароль</span>
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            className={field}
          />
        </label>
        {error && (
          <p role="alert" className="rounded-control bg-warning-soft px-3 py-2">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={pending}
          className="w-full rounded-control bg-accent px-4 py-2.5 font-medium text-surface hover:bg-accent-strong disabled:opacity-60"
        >
          Войти
        </button>
      </form>
    </main>
  );
}
