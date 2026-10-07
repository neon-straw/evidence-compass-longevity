import { readFile } from "node:fs/promises";
import { type Page, expect, test } from "@playwright/test";

const rowButton = (page: Page, n: string) =>
  page.getByRole("button", { name: `Открыть основание строки ${n}` });
const panel = (page: Page) => page.getByRole("region", { name: /^Основание · строка/ });

async function exportCsv(page: Page) {
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Экспорт CSV" }).click(),
  ]);
  return {
    name: download.suggestedFilename(),
    text: await readFile(await download.path(), "utf8"),
  };
}

test.describe("Результат", () => {
  test("клик по строке открывает основание, клик вне панели закрывает без потери фокуса", async ({
    page,
  }) => {
    await page.goto("/result?scenario=result-default");
    const row = (number: string) => page.getByRole("row").filter({ has: rowButton(page, number) });
    await row("01").getByRole("cell").nth(1).click();
    await expect(panel(page)).toContainText("Основание · строка 01");
    await panel(page).getByRole("heading", { name: "Основание · строка 01" }).click();
    await expect(panel(page)).toBeVisible();
    await row("01").getByRole("cell").nth(1).click();
    await expect(panel(page)).toBeHidden();
    await row("01").getByRole("cell").nth(1).click();
    await expect(panel(page)).toContainText("Основание · строка 01");
    await row("02").getByRole("cell").nth(1).click();
    await expect(panel(page)).toContainText("Основание · строка 02");
    await page.getByRole("heading", { name: "Численные показатели", exact: true }).click();
    await expect(panel(page)).toBeHidden();
    await row("02").getByRole("cell").nth(1).click();
    await expect(panel(page)).toContainText("Основание · строка 02");
    const search = page.getByRole("textbox", {
      name: "Поиск по объектам, показателям, публикациям",
    });
    await search.click();
    await expect(panel(page)).toBeHidden();
    await expect(search).toBeFocused();
    await rowButton(page, "02").click();
    await expect(panel(page)).toBeVisible();
    await rowButton(page, "02").click();
    await expect(panel(page)).toBeHidden();
    await rowButton(page, "02").click();
    await expect(panel(page)).toBeVisible();
    await page.getByRole("button", { name: "Карточка публикации Ruby 2018" }).first().click();
    await expect(page.getByRole("dialog", { name: "Карточка публикации" })).toBeVisible();
    await expect(panel(page)).toBeHidden();
  });

  test("блоки идут в порядке макета: вопрос, параметры, краткий вывод, охват, таблица", async ({
    page,
  }) => {
    await page.goto("/result?scenario=result-default");

    await expect(page.getByText("Исследовательский вопрос")).toBeVisible();
    await expect(page.getByText("Параметры поиска")).toBeVisible();
    await expect(page.getByText("Предварительно")).toBeVisible();
    await expect(page.getByRole("button", { name: "Строка 01", exact: true })).toBeVisible();
    await expect(page.getByText("Ещё показателей: 23")).toBeVisible();
    await expect(page.getByRole("link", { name: "Подробнее" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Численные показатели" })).toBeVisible();
    await page.getByRole("button", { name: "Условия", exact: true }).hover();
    await expect(page.getByRole("tooltip")).toContainText(
      "Условия содержания и наблюдения животных",
    );
    await page.mouse.move(0, 0);
    await expect(page.getByText("36 строк", { exact: true })).toBeVisible();
    await expect(page.getByText("Неполный результат")).toHaveCount(0);
    await expect(
      page.getByText("Самостоятельная оценка качества исследований в этот этап не входит", {
        exact: false,
      }),
    ).toBeVisible();
  });

  test("выбор строки открывает основание снизу; закрытие возвращает фокус к строке", async ({
    page,
  }) => {
    await page.goto("/result?scenario=result-default");
    await rowButton(page, "01").click();

    await expect(panel(page)).toContainText("Основание · строка 01");
    await expect(panel(page)).toContainText("shark-complete.pdf, версия 1");
    await expect(panel(page)).toContainText("Точный фрагмент в демо недоступен");
    await expect(panel(page)).toContainText("Проверка извлечения не равна научному подтверждению.");
    await panel(page).getByRole("button", { name: "Показатель", exact: true }).focus();
    await expect(page.getByRole("tooltip")).toContainText("не взаимозаменяемы");
    await panel(page).getByRole("button", { name: "Метод", exact: true }).focus();
    await expect(page.getByRole("tooltip")).toContainText(
      "Как авторы получили или оценили этот показатель",
    );
    await panel(page).getByRole("button", { name: "Ограничения авторов", exact: true }).focus();
    await expect(page.getByRole("tooltip")).toContainText(
      "Ограничения исследования или результата, прямо указанные авторами публикации",
    );

    await panel(page).getByRole("button", { name: "Закрыть основание" }).click();
    await expect(panel(page)).toBeHidden();
    await expect(rowButton(page, "01")).toBeFocused();
  });

  test("экспорт CSV выгружает текущую ревизию таблицы показателей", async ({ page }) => {
    await page.goto("/result?scenario=result-default");
    const { name, text } = await exportCsv(page);

    expect(name).toBe("evidence-compass-revision-1.csv");
    const lines = text.trim().split("\n");
    for (const column of [
      "measure_id",
      "species_name_in_source",
      "taxonomy_note",
      "measure_kind",
      "uncertainty_kind",
    ]) {
      expect(lines[0].split(",")).toContain(column);
    }
    expect(lines).toHaveLength(37);
    expect(lines[0]).toContain("evidence_ids");
    expect(text).toContain("shark-4");
  });

  test("частичный результат: без строк неудачной работы, помечен неполным", async ({ page }) => {
    await page.goto("/result?scenario=result-partial");

    await expect(page.getByText("Неполный результат")).toBeVisible();
    await expect(page.getByText("Работы без показателей в выводе")).toBeVisible();
    const publicationButton = page.getByRole("button", {
      name: "Карточка публикации Nielsen 2016",
    });
    await expect(publicationButton).toBeVisible();
    await publicationButton.click();
    await expect(page.getByRole("dialog", { name: "Карточка публикации" })).toBeVisible();
  });

  test("пустой результат: охват и причины без содержательного вывода", async ({ page }) => {
    await page.goto("/result?scenario=result-empty");

    await expect(page.getByText(/^Нет допустимых строк/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Почему нет вывода" })).toBeVisible();
    await expect(
      page.getByText("Допустимых строк нет. Причины по каждой работе — в охвате."),
    ).toBeVisible();
    await page.getByRole("link", { name: "Подробнее" }).click();
    await expect(page).toHaveURL(/\/result\/coverage$/);
    await expect(page.getByRole("table", { name: "Охват по работам" })).toContainText(
      "Без показателей",
    );
  });

  test("работа, требующая решения, показана первой с расхождением и отложенным", async ({
    page,
  }) => {
    await page.goto("/result?scenario=result-unresolved");

    const list = page.getByText("Работы без показателей в выводе").locator("..");
    await expect(list.getByText("Требует решения")).toBeVisible();
    await expect(list.getByText(/обзор не уточняет/i)).toBeVisible();
    await list.getByText("Не стали показателями").click();
    await expect(list.getByText(/показатель приведён для другого вида/i)).toBeVisible();
  });

  test("устаревший результат читается, причина видна, обновление явное", async ({ page }) => {
    await page.goto("/result?scenario=result-stale");

    await expect(page.getByText("Краткий вывод устарел.")).toBeVisible();
    await expect(page.getByText(/краткий вывод построен по ревизии 1/)).toBeVisible();
    await expect(page.getByText("Устарел", { exact: true })).toBeVisible();
    expect((await exportCsv(page)).name).toBe("evidence-compass-revision-2-stale.csv");

    await page.getByRole("button", { name: "Обновить краткий вывод" }).click();
    await expect(page.getByText("Краткий вывод устарел.")).toBeHidden();
    expect((await exportCsv(page)).name).toBe("evidence-compass-revision-2.csv");
  });

  test("исправление строки сохраняется и делает краткий вывод устаревшим", async ({ page }) => {
    await page.goto("/result?scenario=result-default");
    await rowButton(page, "02").click();
    await panel(page).getByRole("button", { name: "Исправить извлечение" }).click();
    await page
      .getByLabel("Что исправлено и по какому месту источника")
      .fill("Сверено с таблицей S2, строка 5.");
    await page.getByRole("button", { name: "Сохранить исправление" }).click();

    await expect(page.getByText("Краткий вывод устарел.")).toBeVisible();
    await expect(page.getByRole("row").filter({ has: rowButton(page, "02") })).toContainText(
      "Исправлено",
    );
  });

  test("меню строки отмечает извлечение проверенным", async ({ page }) => {
    await page.goto("/result?scenario=result-default");
    await page.getByRole("button", { name: "Действия со строкой 03" }).click();
    await page.getByRole("menuitem", { name: "Отметить: извлечение проверено" }).click();

    await expect(page.getByRole("row").filter({ has: rowButton(page, "03") })).toContainText(
      "Извлечение проверено",
    );
  });

  test("несверенное значение помечено у строки, фильтр сверки и ручная сверка в выводе", async ({
    page,
  }) => {
    await page.goto("/result?scenario=result-default");
    const row = (n: string) => page.getByRole("row").filter({ has: rowButton(page, n) });

    await expect(row("07")).toContainText("Цитата не найдена");
    await expect(row("08")).toContainText("Значение не сверено");
    await expect(row("09")).toContainText("Сверено вручную");
    await expect(row("06")).not.toContainText(/Цитата не найдена|Значение не сверено|вручную/);
    await page.getByText("Ещё показателей: 23").click();
    const finding = (n: string) =>
      page
        .getByRole("listitem")
        .filter({ has: page.getByRole("button", { name: `Строка ${n}`, exact: true }) });
    await expect(finding("09")).toContainText("сверено вручную");
    await expect(finding("06")).not.toContainText("сверено вручную");
    await expect(finding("07")).toHaveCount(0);
    await expect(page.getByText("Строка 09 — сверена с документом пользователем")).toBeVisible();
    await expect(page.getByText(/^Строки 07–08 — число с единицами не найдено/)).toBeAttached();

    await page.getByRole("button", { name: "Фильтры" }).click();
    await page.getByLabel("Сверка значения с документом").selectOption("unmatched");
    await page.keyboard.press("Escape");
    await expect(page.getByText("2 строки из 36")).toBeVisible();
    await expect(row("07")).toBeVisible();
    await expect(row("09")).toHaveCount(0);

    await page.getByRole("button", { name: "Действия со строкой 08" }).click();
    await page.getByRole("menuitem", { name: "Отметить: извлечение проверено" }).click();
    await expect(page.getByText("1 строка из 36")).toBeVisible();
  });

  test("значение не из этой работы помечено в таблице и панели и объяснено в выводе", async ({
    page,
  }) => {
    await page.goto("/result?scenario=result-default");
    const row = page.getByRole("row").filter({ has: rowButton(page, "10") });

    await expect(row).toContainText("Не результат работы");
    await expect(page.getByRole("row").filter({ has: rowButton(page, "06") })).not.toContainText(
      "Не результат работы",
    );
    await expect(
      page.getByText(/^Строка 10 — не собственный результат работы: значение приведено/),
    ).toBeAttached();
    await rowButton(page, "10").click();
    await expect(panel(page)).toContainText("ПроисхождениеНе результат работы");
  });

  test("настройка колонок скрывает колонку и сохраняется", async ({ page }) => {
    await page.goto("/result?scenario=result-default");
    await page.getByRole("button", { name: "Колонки" }).click();
    await page.getByRole("checkbox", { name: "Точность и разброс" }).uncheck();
    await page.keyboard.press("Escape");

    await expect(page.getByRole("columnheader", { name: "Точность и разброс" })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("columnheader", { name: "Точность и разброс" })).toHaveCount(0);
  });

  test("каждая находка ведёт к своей строке, в том числе после раскрытия", async ({ page }) => {
    await page.goto("/result?scenario=result-default");
    await page.getByRole("button", { name: "Строка 33", exact: true }).click();

    await expect(panel(page)).toContainText("Основание · строка 33");
    await expect(panel(page)).toContainText("Демо-фрагмент");
    await page.keyboard.press("Escape");
    await expect(panel(page)).toBeHidden();
    await expect(page.getByRole("button", { name: "Строка 04", exact: true })).toBeHidden();
    await page.getByText("Ещё показателей: 23").click();
    await page.getByRole("button", { name: "Строка 04", exact: true }).click();
    await expect(panel(page)).toContainText("Основание · строка 04");
  });

  test("карточка публикации на результате только для чтения", async ({ page }) => {
    await page.goto("/result?scenario=result-default");
    await page.getByRole("button", { name: "Карточка публикации Ruby 2018" }).first().click();

    const card = page.getByRole("dialog", { name: "Карточка публикации" });
    await expect(card).toContainText("Соответствует");
    await expect(card).toContainText("Документы основания выбранного показателя");
    await expect(card).toContainText("ruby2018.pdf, версия 2");
    await expect(card).not.toContainText("Документы загружаются на шаге");
    await expect(card.getByRole("button", { name: /Изменить$/ })).toHaveCount(0);
  });
});
