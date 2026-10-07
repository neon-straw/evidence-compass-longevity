import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { Navigate, Outlet, useLocation } from "react-router";
import { api, isMockApi } from "../api/client";
import { ExportCsvButton } from "../screens/result/ExportCsvButton";
import { ClearDataButton } from "./ClearDataButton"; // временно
import { ProfileMenu } from "./ProfileMenu";
import { useSession } from "./session";
import { StepNav } from "./StepNav";

function ServerStatus() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/health");
      return data ?? error ?? null;
    },
  });
  if (health.isPending || health.data?.status === "ok") return null;

  return (
    <p role="alert" className="mx-gutter mt-4 rounded-control bg-warning-soft px-4 py-3">
      Сервер недоступен: данные не загружаются. Проверьте, что API запущен, и обновите страницу.
    </p>
  );
}

/** Общий корень для входа и рабочих экранов: статус сервера виден везде. */
export function RootLayout() {
  return (
    <div className="min-h-screen">
      <ServerStatus />
      <Outlet />
    </div>
  );
}

export function AppShell() {
  const session = useSession();
  // «Экспорт CSV» — действие экрана «Результат», а не глобальная команда (shape §2).
  const onResult = useLocation().pathname.startsWith("/result");
  // При сбое сервера остаётся только сообщение ServerStatus: это не то же, что «вход не выполнен».
  if (session.isPending || session.isError) return null;
  if (!session.data) return <Navigate to="/login" replace />;

  return (
    <>
      <header className="flex items-center gap-4 border-b border-line bg-surface px-6 py-3">
        <span className="text-lg font-semibold">Evidence Compass</span>
        {isMockApi && (
          <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-caption font-medium text-accent">
            Демо
          </span>
        )}
        <div className="ml-auto flex items-center gap-4">
          {!isMockApi && <ClearDataButton /> /* временно */}
          <button type="button" className="flex items-center gap-1.5 text-ink-muted hover:text-ink">
            <History aria-hidden className="size-4" />
            История
          </button>
          {onResult && <ExportCsvButton />}
          <span aria-hidden className="h-6 w-px bg-line" />
          <ProfileMenu login={session.data.login} />
        </div>
      </header>
      <StepNav />
      <main className="flex min-h-[calc(100vh-6.75rem)] flex-col gap-gutter p-gutter">
        <Outlet />
      </main>
    </>
  );
}
