import type { ClientRequest, IncomingMessage } from "node:http";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // API сверяет Origin с Host (ADR 0008). http-proxy иначе ставит Host цели
      // (localhost:8000), и вход отвечает 403, который экран показывает как «сервер недоступен».
      "/api": {
        target: "http://127.0.0.1:8000",
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq: ClientRequest, req: IncomingMessage) => {
            if (req.headers.host) proxyReq.setHeader("host", req.headers.host);
          });
        },
      },
    },
  },
});
