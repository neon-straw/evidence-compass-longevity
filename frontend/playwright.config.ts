import { defineConfig, devices } from "@playwright/test";

const port = 5174;

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: `http://localhost:${port}`, locale: "ru-RU" },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: `pnpm dev:mock --port ${port} --strictPort`,
    port,
    reuseExistingServer: !process.env.CI,
  },
});
