import { type Page, expect, test } from "@playwright/test";

const goToSelection = (page: Page) => page.getByRole("button", { name: "Перейти к отбору" });
const proceed = (page: Page) => page.getByRole("button", { name: /^Перейти к документам/ });

async function runSearch(page: Page, scenario: string) {
  await page.goto(`/search?scenario=${scenario}`);
  await page.getByRole("button", { name: "Запустить поиск" }).click();
}

test.describe("Поиск", () => {
  test("ограниченная выдача показывает найденное и сохранённое отдельно", async ({ page }) => {
    await runSearch(page, "search-limited");
    await expect(page.getByText("Поиск завершён", { exact: true })).toBeVisible();
    await expect(page.getByText("Демо: Найдено 630, сохранено 200")).toBeVisible();
    await expect(page.getByText(/Неполный охват: Europe PMC/)).toBeVisible();
    await expect(goToSelection(page)).toBeEnabled();
    await page.reload();
    await expect(page.getByText("Демо: Найдено 630, сохранено 200")).toBeVisible();
  });

  test("запрос Europe PMC использует собственные поля", async ({ page }) => {
    await page.goto("/search?scenario=search-ready");
    await page.getByRole("button", { name: "Открыть запрос Europe PMC" }).click();
    const dialog = page.getByRole("dialog", { name: "Запрос Europe PMC" });
    await expect(dialog).toContainText('TITLE_ABS:"Heterocephalus glaber"');
    await expect(dialog).not.toContainText("[tiab]");
  });

  test("выполняется с независимым прогрессом ресурсов и после завершения открывает отбор", async ({
    page,
  }) => {
    await runSearch(page, "search-ready");

    await expect(page.getByText("Поиск выполняется")).toBeVisible();
    await expect(page.getByRole("progressbar", { name: "Ход поиска" })).toBeVisible();
    await expect(goToSelection(page)).toBeDisabled();
    await expect(goToSelection(page)).toHaveAccessibleDescription("Дождитесь завершения поиска.");

    await expect(page.getByText("Поиск завершён", { exact: true })).toBeVisible();
    await expect(page.getByText("Результаты сохранены для версии критериев 1.")).toBeVisible();
    await expect(page.getByText("Демо: 18 записей")).toBeVisible();
    await expect(page.getByText("Демо: 12 записей")).toBeVisible();

    await goToSelection(page).click();
    await expect(page).toHaveURL(/\/selection$/);
    await expect(page.getByRole("heading", { name: "Отбор публикаций" })).toBeVisible();
  });

  test("остановка требует подтверждения и оставляет повтор", async ({ page }) => {
    await runSearch(page, "search-ready");
    await page.getByRole("button", { name: "Остановить поиск" }).click();
    await page.getByRole("button", { name: "Остановить", exact: true }).click();

    await expect(page.getByText("Поиск остановлен", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Повторить поиск" })).toBeVisible();
    await expect(goToSelection(page)).toBeDisabled();
  });

  test("открыть запрос показывает фактический текст запроса ресурса", async ({ page }) => {
    await page.goto("/search?scenario=search-ready");
    await page.getByRole("button", { name: "Открыть запрос PubMed" }).click();

    await expect(page.getByRole("dialog", { name: "Запрос PubMed" })).toContainText(
      '"Heterocephalus glaber"[tiab]',
    );
  });

  test("пустая выдача — не ошибка: переход к отбору ведёт к ручному добавлению", async ({
    page,
  }) => {
    await runSearch(page, "search-empty");

    await expect(page.getByText("Поиск завершён: записей не найдено")).toBeVisible();
    await goToSelection(page).click();
    await expect(page.getByText("Поиск не нашёл записей")).toBeVisible();
    await expect(page.getByRole("button", { name: "Добавить вручную" }).last()).toBeVisible();
  });

  test("частичный отказ: ошибка одного ресурса видна, найденное доступно", async ({ page }) => {
    await runSearch(page, "search-partial");

    await expect(page.getByText("Поиск завершён частично")).toBeVisible();
    await expect(page.getByText("Ресурс не ответил.")).toBeVisible();
    await expect(goToSelection(page)).toBeEnabled();
  });

  test("полный отказ: записей нет, переход закрыт с причиной", async ({ page }) => {
    await runSearch(page, "search-failed");

    await expect(page.getByText("Поиск не выполнен")).toBeVisible();
    await expect(goToSelection(page)).toBeDisabled();
    await expect(goToSelection(page)).toHaveAccessibleDescription(
      /Оба ресурса завершились ошибкой/,
    );
    await expect(page.getByRole("button", { name: "Изменить критерии" })).toBeVisible();
  });
});

test.describe("Отбор", () => {
  test("статус «Соответствие» меняется нажатием на сам статус, причина уточняется в карточке", async ({
    page,
  }) => {
    await page.goto("/selection?scenario=selection-default");
    const title = "Compromised survivorship in zoo elephants.";
    await page.getByRole("button", { name: `Соответствует ли «${title}»: Да. Изменить` }).click();
    await page.getByRole("menuitemradio", { name: "Нет" }).click();

    await expect(
      page.getByRole("button", { name: `Соответствует ли «${title}»: Нет. Изменить` }),
    ).toBeVisible();
    await expect(page.getByRole("checkbox", { name: `Выбрать «${title}»` })).not.toBeChecked();
    await expect(page.getByText("Выбрано 3 из 8", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: `Открыть карточку публикации «${title}»` }).click();
    const card = page.getByRole("dialog", { name: "Карточка публикации" });
    await expect(card).toContainText("пользователь, версия критериев 1");
    await expect(card.getByRole("definition").filter({ hasText: /^не выбрана/ })).toBeVisible();
    await expect(card).toContainText("Исключённую работу выбрать нельзя");
    await card.getByRole("button", { name: "Уточнить" }).click();
    await card.getByLabel("Обоснование решения").fill("Нет показателей для выбранных видов.");
    await card.getByRole("button", { name: "Сохранить обоснование" }).click();
    await expect(card).toContainText("Нет показателей для выбранных видов.");
  });

  test("лимит 8: девятый выбор заблокирован с объяснением, остальные записи видны", async ({
    page,
  }) => {
    await page.goto("/selection?scenario=selection-limit");

    await expect(page.getByText("Выбрано 8 из 8", { exact: true })).toBeVisible();
    const blocked = page
      .getByRole("row")
      .filter({ has: page.getByRole("button", { name: /: Да\. Изменить/ }) })
      .getByRole("checkbox", { disabled: true })
      .first();
    await expect(blocked).toHaveAccessibleDescription(/Выбрано 8 из 8/);
    await expect(page.getByRole("row")).toHaveCount(25);
  });

  test("карточка открывается справа, связь версий подтверждается, фокус возвращается", async ({
    page,
  }) => {
    await page.goto("/selection?scenario=selection-limit");
    const title = "Демо · Loxodonta africana: comparative longevity and cancer resistance";
    const icon = page.getByRole("button", { name: `Открыть карточку публикации «${title}»` });
    await icon.click();

    const card = page.getByRole("dialog", { name: "Карточка публикации" });
    await expect(card).toContainText("Возможно, другая версия этой работы");
    await card.getByRole("button", { name: "Это одна работа" }).click();
    await expect(card).toContainText("Связь версий подтверждена");

    await card.getByRole("button", { name: "Закрыть карточку" }).last().click();
    await expect(card).toBeHidden();
    await expect(icon).toBeFocused();
  });

  test("без выбранных работ переход к документам недоступен", async ({ page }) => {
    await page.goto("/selection?scenario=selection-none-included");

    await expect(proceed(page)).toBeDisabled();
    await expect(proceed(page)).toHaveAccessibleDescription(/Выберите от одной до восьми работ/);
  });

  test("выбор записи, ждущей решения, включает её от имени пользователя", async ({ page }) => {
    await page.goto("/selection?scenario=selection-none-included");
    const row = page.getByRole("row").filter({ hasText: "Compromised survivorship" });

    await row.getByRole("checkbox").click();

    await expect(row.getByRole("checkbox")).toBeChecked();
    await expect(row).toContainText("Да");
    await expect(page.getByText("Выбрано 1 из 8", { exact: true })).toBeVisible();
    await expect(proceed(page)).toBeEnabled();
  });

  test("ручное добавление создаёт запись с постоянной меткой", async ({ page }) => {
    await page.goto("/selection?scenario=selection-default");
    await page.getByRole("button", { name: "Добавить вручную" }).click();
    await page.getByRole("textbox", { name: "DOI, PMID или название" }).fill("Balaena");
    await page.getByRole("button", { name: "Найти" }).click();
    await page.getByRole("button", { name: /^Добавить «Демо · Balaena/ }).click();

    const row = page.getByRole("row").filter({ hasText: "Balaena mysticetus" });
    await expect(row).toContainText("Добавлено вручную");
    await expect(row).toContainText("Не решено");
  });

  test("открытый текст — сведения источника, по ним можно отфильтровать", async ({ page }) => {
    await page.goto("/selection?scenario=selection-default");
    const ruby = page.getByRole("row").filter({ hasText: "Naked Mole-Rat mortality" });
    await expect(page.getByRole("columnheader", { name: /Открытый текст/ })).toBeVisible();
    await expect(ruby).toContainText("Есть");

    await page.getByRole("button", { name: "Фильтры" }).click();
    await page.getByRole("checkbox", { name: "Нет", exact: true }).check();

    await expect(ruby).toBeHidden();
    await expect(page.getByText(/Скрыто фильтрами: \d+/)).toBeVisible();
  });

  test("фильтры не сбрасывают выбор, видны числом скрытых строк и сохраняются при возврате", async ({
    page,
  }) => {
    await page.goto("/selection?scenario=selection-default");
    await page.getByRole("combobox", { name: "Решение" }).selectOption("excluded");

    await expect(page.getByText(/Скрыто фильтрами: \d+/)).toBeVisible();
    await expect(page.getByText("Выбрано 4 из 8", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Вернуться к поиску" }).click();
    await page.getByRole("button", { name: "Перейти к отбору" }).click();
    await expect(page.getByRole("combobox", { name: "Решение" })).toHaveValue("excluded");
  });

  test("ошибка ресурса раскрывается по действию", async ({ page }) => {
    await runSearch(page, "search-partial");
    await page.getByRole("button", { name: "Показать диагностику" }).click();

    await expect(page.getByText("Europe PMC ответил 503: сервис недоступен.")).toBeVisible();
  });

  test("переход к документам открывает шаг «Подготовка»", async ({ page }) => {
    await page.goto("/selection?scenario=selection-default");
    await proceed(page).click();

    await expect(page).toHaveURL(/\/preparation$/);
  });
});
