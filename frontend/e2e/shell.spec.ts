import { expect, test } from "@playwright/test";

test("оболочка открывает шаг «Вопрос» и показывает шесть шагов по порядку", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveURL(/\/question$/);
  const steps = page.getByRole("navigation", { name: "Шаги исследования" }).getByRole("listitem");
  await expect(steps).toHaveText([
    "Вопрос",
    "Критерии",
    "Поиск",
    "Отбор",
    "Подготовка",
    "Результат",
  ]);
  await expect(page.getByRole("link", { name: "Вопрос" })).toHaveAttribute("aria-current", "page");
});

test("недостигнутый шаг недоступен и объясняет условие", async ({ page }) => {
  await page.goto("/question");

  const criteria = page.getByRole("link", { name: "Критерии" });
  await expect(criteria).toHaveAttribute("aria-disabled", "true");
  await expect(criteria).toHaveAccessibleDescription("Доступно после подготовки критериев");

  await criteria.focus();
  await expect(criteria).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText("Доступно после подготовки критериев");
});

test("прямая ссылка на недостигнутый шаг возвращает к последнему достигнутому", async ({
  page,
}) => {
  await page.goto("/result");

  await expect(page).toHaveURL(/\/question$/);
});

test("в демо-режиме API отвечает через MSW без сервера", async ({ page }) => {
  const health = page.waitForResponse("**/api/health");
  await page.goto("/");

  expect((await health).status()).toBe(200);
  await expect(page.getByText("Демо", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test.describe("без MSW", () => {
  test.use({ serviceWorkers: "block" });

  test("недоступный сервер показывает сообщение", async ({ page }) => {
    await page.route("**/api/health", (route) =>
      route.fulfill({ status: 503, json: { status: "degraded", database: "unavailable" } }),
    );
    await page.goto("/");

    await expect(page.getByRole("alert")).toContainText("Сервер недоступен");
  });
});
