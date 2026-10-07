import { type Page, expect, test } from "@playwright/test";

const startButton = (page: Page) => page.getByRole("button", { name: /^Запустить извлечение/ });
const row = (page: Page, name: string) => page.getByRole("row").filter({ hasText: name });
/** Строка работы в таблице документов: итог обработки показывается в ней (shape §3.5). */
const workRow = (page: Page, name: string) =>
  page
    .getByRole("row")
    .filter({ hasText: name })
    .filter({ has: page.getByRole("button", { name: /^Загрузить приложение/ }) });
/** Переключатель строки работы: готовые работы свёрнуты по умолчанию. */
const workToggle = (page: Page, label: RegExp) => page.getByRole("button", { name: label });

async function start(page: Page, scenario: string) {
  await page.goto(`/preparation?scenario=${scenario}`);
  await startButton(page).click();
}

test.describe("Документы", () => {
  test("действия строки: повтор загрузки, ручная загрузка новой версии, проверка файла", async ({
    page,
  }) => {
    await page.goto("/preparation?scenario=extraction-ready");
    await expect(
      page.getByText("Демо: 8 документов · 4 готовы · 1 требует внимания"),
    ).toBeVisible();
    await expect(startButton(page)).toHaveText(/Демо: 3 работы/);

    const elephant = row(page, "elephant-published.pdf");
    await elephant.getByRole("button", { name: /^Повторить загрузку/ }).click();
    await expect(elephant).toContainText("Загружается");
    await expect(elephant).toContainText("Готов");

    // Таблица в XLSX не загружалась: формат не читается, повтор ничего не даст.
    const supplement = row(page, "clubb2008-tables-S1.xlsx");
    await expect(supplement).toContainText("Не читается");
    await expect(supplement).toContainText("сохраните таблицу как CSV или PDF");
    await expect(supplement.getByRole("button", { name: /^Повторить загрузку/ })).toHaveCount(0);

    await supplement.getByRole("button", { name: /^Загрузить вручную/ }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "clubb2008-tables-S1.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      buffer: Buffer.from("demo"),
    });
    await page.getByRole("button", { name: "Загрузить", exact: true }).click();
    // Таблица в XLSX не читается: это пробел работы с подсказкой, а не «Готов».
    const uploaded = row(page, "clubb2008-tables-S1.xlsx");
    await expect(uploaded).toContainText("Не читается");
    await expect(uploaded).toContainText("v1");
    await expect(uploaded).toContainText("сохраните таблицу как CSV или PDF");
    await expect(row(page, "Clubb 2008")).toContainText("1 приложение с таблицами не прочитано");

    await row(page, "podlutsky2005.pdf")
      .getByRole("button", { name: /^Проверить/ })
      .click();
    await page.getByRole("button", { name: "Да, документ этой работы" }).click();
    await expect(row(page, "podlutsky2005.pdf")).toContainText("Готов");
    await expect(startButton(page)).toHaveText(/Демо: 4 работы/);
  });

  test("документы сгруппированы по работам; готовая работа свёрнута", async ({ page }) => {
    await page.goto("/preparation?scenario=extraction-ready");

    // Работа с пробелом раскрыта, готовая — одна строка с состоянием.
    await expect(row(page, "Clubb 2008")).toContainText("Готова к извлечению");
    await expect(row(page, "Clubb 2008")).toContainText("1 приложение с таблицами не прочитано");
    await expect(row(page, "elephant-published.pdf")).toBeVisible();
    await expect(row(page, "Ruby 2018")).toContainText("4 документа");
    await expect(row(page, "Ruby 2018")).toContainText("Готова к извлечению");
    await expect(row(page, "ruby-supp1.csv")).toHaveCount(0);

    await workToggle(page, /^Ruby 2018/).click();
    await expect(workToggle(page, /^Ruby 2018/)).toHaveAttribute("aria-expanded", "true");
    // Изображение не загружается и не читается, но это не пробел: нейтрально, без повтора.
    const figure = row(page, "elife-31157-fig1.jpg");
    await expect(figure).toContainText("Не читается");
    await expect(figure).toContainText("Изображение: значения из него не извлекаются.");
    await expect(figure.getByRole("button", { name: /^Повторить загрузку/ })).toHaveCount(0);
    await expect(row(page, "ruby-supp1.csv")).toContainText("Готов");

    await workToggle(page, /^Ruby 2018/).click();
    await page.getByLabel("Поиск по документам и публикациям").fill("supp1");
    await expect(row(page, "Ruby 2018")).toBeVisible();
    await expect(row(page, "ruby-supp1.csv")).toBeVisible();
    await expect(row(page, "Clubb 2008")).toHaveCount(0);
  });

  test("приложение, которого система не нашла, загружается к работе", async ({ page }) => {
    await page.goto("/preparation?scenario=extraction-ready");

    await page
      .getByRole("button", { name: /^Загрузить приложение к работе «Nielsen 2016/ })
      .click();
    await expect(page.getByRole("dialog", { name: "Загрузить приложение" })).toContainText(
      "Nielsen 2016",
    );
    await page.locator('input[type="file"]').setInputFiles({
      name: "shark-table-S2.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("id,age\n1,392\n"),
    });
    await page.getByRole("button", { name: "Загрузить", exact: true }).click();

    // Новое приложение ждёт подтверждения принадлежности; работа раскрывается с пробелом.
    await expect(row(page, "shark-table-S2.csv")).toContainText("Требует проверки");
    await expect(row(page, "Nielsen 2016")).toContainText("1 приложение требует проверки");
    await expect(
      row(page, "shark-table-S2.csv").getByRole("button", { name: /^Повторить загрузку/ }),
    ).toHaveCount(0);
  });

  test("пробелы документов видны по отдельности, загрузка завершается", async ({ page }) => {
    await page.goto("/preparation?scenario=extraction-gaps");

    const closed = row(page, "elephant-published.pdf");
    await expect(closed).toContainText("Не найден");
    // Закрытая работа: PDF только в браузере — открыть его и загрузить вручную.
    await expect(closed).toContainText("PMC отдаёт этот PDF только в браузере");
    const open = closed.getByRole("link", { name: /^Открыть PDF/ });
    await expect(open).toHaveAttribute("target", "_blank");
    await expect(open).toHaveAttribute("href", /pmc\.ncbi\.nlm\.nih\.gov/);
    await expect(closed.getByRole("button", { name: /^Загрузить вручную/ })).toBeEnabled();
    // Повтор остаётся, но последним: он даст тот же ответ.
    await expect(
      closed.getByRole("link").or(closed.getByRole("button")).last(),
    ).toHaveAccessibleName(/^Повторить загрузку/);
    await expect(row(page, "ruby-supp1.csv")).toContainText("Ошибка");
    await expect(row(page, "podlutsky2005.pdf")).toContainText("Требует проверки");
    // Загрузка — не повод раскрывать работу: её состояние видно в строке работы.
    await expect(row(page, "Nielsen 2016")).toContainText("Документы загружаются");
    await expect(row(page, "Nielsen 2016")).toContainText("Готова к извлечению");
    await expect(row(page, "shark-complete.pdf")).toHaveCount(0);
  });
});

test.describe("Операция", () => {
  test("пауза запрашивается, подтверждается и снимается; остановка требует подтверждения", async ({
    page,
  }) => {
    await start(page, "extraction-ready");
    await expect(page.getByText("Извлечение выполняется", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Загрузить приложение к работе «Ruby 2018/ }),
    ).toBeDisabled();
    await workToggle(page, /^Ruby 2018/).click();
    await expect(
      row(page, "ruby2018.pdf").getByRole("button", { name: /^Загрузить вручную/ }),
    ).toBeDisabled();

    await page.getByRole("button", { name: "Приостановить" }).click();
    await expect(page.getByText("Пауза запрошена", { exact: true })).toBeVisible();
    await expect(page.getByText("Извлечение на паузе", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await expect(page.getByText("Извлечение выполняется", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Остановить", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Остановить извлечение?" })).toContainText(
      "Готовые результаты и ваши исправления сохранятся",
    );
    await page.getByRole("dialog").getByRole("button", { name: "Остановить" }).click();
    await expect(page.getByText("Извлечение остановлено", { exact: true })).toBeVisible();
    await expect(page.getByRole("table", { name: "Документы" })).toContainText("Не обработана");

    await page.getByRole("button", { name: /^Запустить необработанные/ }).click();
    await expect(page.getByText("Извлечение завершено частично")).toBeVisible();
  });

  test("ожидание модели продолжается только явно", async ({ page }) => {
    await page.goto("/preparation?scenario=extraction-waiting-llm");

    await expect(page.getByText("Ожидание модели", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Продолжить", exact: true }).click();
    await expect(page.getByText("Извлечение выполняется", { exact: true })).toBeVisible();
  });

  test("на паузе документы доступны только для чтения, с объяснением", async ({ page }) => {
    await page.goto("/preparation?scenario=extraction-paused");

    await expect(page.getByText("Извлечение на паузе", { exact: true })).toBeVisible();
    await workToggle(page, /^Ruby 2018/).click();
    const upload = row(page, "ruby2018.pdf").getByRole("button", { name: /^Загрузить вручную/ });
    await expect(upload).toBeDisabled();
    await expect(upload).toHaveAccessibleDescription(/нельзя менять/);
  });

  test("частичный отказ: итог каждой работы отдельно, повтор не затрагивает готовое", async ({
    page,
  }) => {
    await start(page, "extraction-partial");

    await expect(page.getByText("Извлечение завершено частично")).toBeVisible();
    await expect(workRow(page, "Nielsen")).toContainText("Ошибка");
    await expect(workRow(page, "Clubb")).toContainText("Без показателей");
    // Итог работы — в её строке таблицы, отдельного списка работ нет (shape §3.5).
    await expect(page.getByRole("list", { name: "Работы" })).toHaveCount(0);
    const bat = workRow(page, "Podlutsky");
    await expect(bat).toContainText("Не обработана");
    await expect(bat).toContainText("требует проверки");
    await expect(page.getByText(/^Текущий результат неполный/)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Открыть текущий результат/ })).toBeEnabled();

    await page.getByRole("button", { name: "Повторить неудачные" }).click();
    await expect(workRow(page, "Nielsen")).toContainText("29 показателей");
    await expect(workRow(page, "Ruby")).toContainText("3 показателя");
  });

  test("завершено без показателей: переход к результату с охватом", async ({ page }) => {
    await start(page, "extraction-no-measures");

    await expect(page.getByText("Извлечение завершено без показателей")).toBeVisible();
    await page.getByRole("button", { name: /^Открыть текущий результат/ }).click();
    await expect(page).toHaveURL(/\/result$/);
  });

  test("журнал операции открывается отдельным представлением и пополняется", async ({ page }) => {
    await start(page, "extraction-ready");
    await page.getByRole("link", { name: "Журнал операции" }).click();

    await expect(page).toHaveURL(/\/preparation\/log$/);
    const events = page.getByRole("list", { name: "События" });
    await expect(events).toContainText("Извлечение поставлено в очередь");
    await expect(events).toContainText("Извлечение завершено.");
    await page.getByRole("button", { name: "Вернуться к подготовке" }).click();
    await expect(page).toHaveURL(/\/preparation$/);
  });
});
