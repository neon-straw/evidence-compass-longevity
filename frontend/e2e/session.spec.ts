import { expect, test } from "@playwright/test";

test("выход из меню профиля ведёт на экран входа, повторный вход возвращает к шагам", async ({
  page,
}) => {
  await page.goto("/");

  const profile = page.getByRole("button", { name: "Профиль: demo.researcher" });
  await expect(profile).toHaveText("DR");
  await profile.click();
  await page.getByRole("menuitem", { name: "Выйти" }).click();

  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Логин").fill("a.chernov");
  await page.getByLabel("Пароль").fill("secret");
  await page.getByRole("button", { name: "Войти" }).click();

  await expect(page).toHaveURL(/\/question$/);
  await expect(page.getByRole("button", { name: "Профиль: a.chernov" })).toHaveText("AC");
});

test.describe("без MSW", () => {
  test.use({ serviceWorkers: "block" });

  test.beforeEach(async ({ page }) => {
    await page.route("**/api/health", (route) =>
      route.fulfill({ json: { status: "ok", database: "ok" } }),
    );
    await page.route("**/api/session", (route) =>
      route.fulfill({ status: 401, json: { detail: "Требуется вход" } }),
    );
  });

  test("без входа рабочие шаги недоступны", async ({ page }) => {
    await page.goto("/result");

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("navigation", { name: "Шаги исследования" })).toHaveCount(0);
  });

  test("неверный пароль показывает ошибку и оставляет на экране входа", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Логин").fill("a.chernov");
    await page.getByLabel("Пароль").fill("wrong");
    await page.getByRole("button", { name: "Войти" }).click();

    await expect(page.getByRole("alert")).toHaveText("Неверный логин или пароль.");
    await expect(page).toHaveURL(/\/login$/);
  });
});
