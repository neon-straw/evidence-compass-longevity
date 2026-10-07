import { type Page, expect, test } from "@playwright/test";

/** Открывает критерии, подготовленные системой в заданном демо-сценарии. */
async function openCriteria(page: Page, scenario = "default") {
  await page.goto(`/question?scenario=${scenario}`);
  await page.getByRole("button", { name: "Подготовить критерии" }).click();
  await expect(page).toHaveURL(/\/criteria$/);
  await expect(
    page.getByRole("textbox", { name: "Поисковые термины: Продолжительность жизни и возраст" }),
  ).toBeVisible();
}

const confirm = (page: Page) =>
  page.getByRole("button", { name: "Подтвердить и перейти к поиску" });

test.describe("Вопрос", () => {
  test("пустой вопрос показывает пример, черновик сохраняется автоматически", async ({ page }) => {
    await page.goto("/question?scenario=empty-question");
    const question = page.getByRole("textbox", { name: "Исследовательский вопрос" });
    await expect(question).toHaveAttribute("placeholder", /^Например:/);
    await expect(page.getByRole("button", { name: "Подготовить критерии" })).toBeDisabled();

    await question.fill("Какие показатели продолжительности жизни опубликованы для туатары?");
    await expect(page.getByText("Черновик сохранён")).toBeVisible();

    await page.reload();
    await expect(question).toHaveValue(
      "Какие показатели продолжительности жизни опубликованы для туатары?",
    );
  });

  test("вопрос вне формы Stage 1 получает объяснение и остаётся на шаге", async ({ page }) => {
    await page.goto("/question?scenario=unsupported-question");
    await page.getByRole("button", { name: "Подготовить критерии" }).click();

    await expect(page.getByRole("alert")).toContainText("Stage 1 отвечает только на вопросы");
    await expect(page).toHaveURL(/\/question$/);
    await expect(page.getByRole("textbox", { name: "Исследовательский вопрос" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  test("сбой подготовки сохраняет вопрос и предлагает повтор или ручное заполнение", async ({
    page,
  }) => {
    await page.goto("/question?scenario=prepare-failed");
    await page.getByRole("button", { name: "Подготовить критерии" }).click();

    await expect(page.getByRole("alert")).toContainText("Модель не ответила");
    await expect(page.getByRole("button", { name: "Повторить" })).toBeVisible();
    await page.getByRole("button", { name: "Заполнить критерии вручную" }).click();

    await expect(page).toHaveURL(/\/criteria$/);
    await expect(page.getByRole("button", { name: "Добавить критерий" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: /^Критерий \d/ })).toHaveCount(0);
  });
});

test.describe("Критерии", () => {
  test("поисковые названия редактируются и сохраняются при новой редакции", async ({ page }) => {
    await openCriteria(page);
    const aliases = page.getByRole("textbox", {
      name: "Поисковые названия объекта 1",
      exact: true,
    });
    await aliases.fill("naked mole rat; naked mole-rat");
    await expect(page.getByText("Все изменения сохранены")).toBeVisible();
    await page.reload();
    await expect(aliases).toHaveValue("naked mole rat; naked mole-rat");
    await page.getByRole("button", { name: "Переформулировать по вопросу" }).click();
    await page.getByRole("button", { name: "Применить редакцию" }).click();
    await expect(aliases).toHaveValue("naked mole rat; naked mole-rat");
    await expect(page.getByText("Все изменения сохранены")).toBeVisible();
    await confirm(page).click();
    await expect(page).toHaveURL(/\/search$/);
    await page.getByRole("button", { name: "Открыть запрос PubMed" }).click();
    await expect(page.getByRole("dialog")).toContainText("naked mole rat");
  });

  test("предложения системы появляются после подготовки, поля остаются редактируемыми", async ({
    page,
  }) => {
    await page.goto("/question?scenario=default");
    await page.getByRole("button", { name: "Подготовить критерии" }).click();

    await expect(page.getByLabel("Критерии готовятся")).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Условия" })).toBeEnabled();
    await expect(page.getByRole("textbox", { name: "Научное название объекта 1" })).toHaveValue(
      /Heterocephalus glaber/,
    );
    await expect(page.getByRole("link", { name: "Вопрос" })).toBeVisible();
  });

  test("критерий и термины редактируются прямо в полях и сохраняются", async ({ page }) => {
    await openCriteria(page);
    const label = page.getByRole("textbox", { name: "Критерий 1", exact: true });
    await label.fill("Продолжительность жизни");
    const terms = page.getByRole("textbox", { name: "Поисковые термины: Продолжительность жизни" });
    await terms.fill("lifespan; longevity; life expectancy");

    await expect(page.getByText("изменено")).toBeVisible();
    await expect(page.getByText("Добавлено: life expectancy.")).toBeVisible();
    await expect(page.getByText("Убрано из предложенных: age estimate, survival.")).toBeVisible();
    await expect(page.getByText("Все изменения сохранены")).toBeVisible();

    await page.reload();
    await expect(terms).toHaveValue("lifespan; longevity; life expectancy");
  });

  test("без ресурса или с непредставимым критерием подтверждение заблокировано", async ({
    page,
  }) => {
    await openCriteria(page);
    await page.getByRole("checkbox", { name: "PubMed" }).uncheck();
    await page.getByRole("checkbox", { name: "Europe PMC" }).uncheck();

    await expect(page.getByText("Выберите хотя бы один ресурс поиска.").first()).toBeVisible();
    await expect(confirm(page)).toBeDisabled();
    await expect(confirm(page)).toHaveAccessibleDescription(/ресурс поиска/);

    await page.getByRole("checkbox", { name: "PubMed" }).check();
    await page
      .getByRole("textbox", { name: "Поисковые термины: Продолжительность жизни и возраст" })
      .fill("");

    await expect(
      page.getByRole("textbox", { name: "Поисковые термины: Продолжительность жизни и возраст" }),
    ).toHaveAccessibleDescription(/нельзя представить в запросе/);
    await expect(confirm(page)).toBeDisabled();
  });

  test("частично полученные предложения называют конкретный пробел", async ({ page }) => {
    await openCriteria(page, "criteria-partial");

    await expect(page.getByText("Часть предложений не получена")).toBeVisible();
    await expect(page.getByText(/научное название для «Ночница Брандта»/)).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Научное название объекта 3" })).toHaveValue("");
  });

  test("большой объём: объекты с поиском и счётчиком, длинные термины в многострочном поле", async ({
    page,
  }) => {
    await openCriteria(page, "criteria-large");

    await expect(page.getByText("Объектов: 24")).toBeVisible();
    await page.getByRole("textbox", { name: "Найти объект" }).fill("кит");
    await expect(page.getByRole("textbox", { name: /^Название объекта/ })).toHaveCount(2);
    await expect(page.getByText(/Терминов: \d+/).first()).toBeVisible();

    await expect(page.getByText("Критериев: 21")).toBeVisible();
    await page.getByRole("textbox", { name: "Найти критерий" }).fill("radiocarbon");
    await expect(page.getByRole("textbox", { name: /^Критерий \d/ })).toHaveCount(3);
  });

  test("переформулировка не перезаписывает ручные правки без подтверждения", async ({ page }) => {
    await openCriteria(page);
    await page.getByRole("textbox", { name: "Критерий 1", exact: true }).fill("Виды");
    await page.getByRole("button", { name: "Переформулировать по вопросу" }).click();

    await page.getByRole("button", { name: "Оставить мои критерии" }).click();
    await expect(page.getByRole("textbox", { name: "Критерий 1", exact: true })).toHaveValue(
      "Виды",
    );

    await page.getByRole("button", { name: "Переформулировать по вопросу" }).click();
    await page.getByRole("button", { name: "Применить редакцию" }).click();
    await expect(page.getByRole("textbox", { name: "Критерий 1", exact: true })).toHaveValue(
      "Продолжительность жизни и возраст",
    );
  });

  test("подтверждение открывает шаг «Поиск» и отмечает пройденные шаги", async ({ page }) => {
    await openCriteria(page);
    await confirm(page).click();

    await expect(page).toHaveURL(/\/search$/);
    await expect(page.getByRole("link", { name: "Поиск" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("link", { name: "Критерии" })).not.toHaveAttribute("aria-disabled");

    await page.getByRole("link", { name: "Критерии" }).click();
    await expect(page.getByText("Версия 1", { exact: true })).toBeVisible();
    await page.getByRole("checkbox", { name: "Europe PMC" }).uncheck();
    await expect(page.getByText("Версия 1 · есть неподтверждённые изменения")).toBeVisible();
  });
});
