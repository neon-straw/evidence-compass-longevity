import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { isMockApi } from "./api/client";
import { router } from "./app/router";
import "./index.css";

async function startMockApi() {
  if (!isMockApi) return;
  const { setupWorker } = await import("msw/browser");
  const { handlers } = await import("./mocks/handlers");
  await setupWorker(...handlers).start({ onUnhandledRequest: "bypass", quiet: true });
}

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

startMockApi()
  .catch((error: unknown) => console.error("Демо-API не запущен", error))
  .finally(() => {
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </StrictMode>,
    );
  });
