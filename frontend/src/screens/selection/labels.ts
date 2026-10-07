import { PROVENANCE_LABEL } from "../../api/labels";
import type { Decision, Schemas } from "../../api/types";
import type { Tone } from "../../ui/StatusPill";

type Row = Schemas["PublicationRow"];

/** `label` — значение в колонке «Соответствует», `long` — в фильтре, где заголовка колонки нет. */
export const DECISIONS: Record<Decision, { label: string; long: string; tone: Tone }> = {
  included: { label: "Да", long: "Соответствует", tone: "success" },
  excluded: { label: "Нет", long: "Не соответствует", tone: "neutral" },
  needs_review: { label: "Не решено", long: "Не решено", tone: "warning" },
};

/** Одна ширина плашек в колонке, по самому длинному значению («Нет данных», «Не решено»). */
export const PILL_WIDTH = "w-36";

export const DECISION_HINT =
  "Подходит ли работа к вопросу и подтверждённым критериям. Решение сохраняется с причиной и версией критериев; соответствующих работ может быть больше восьми.";
export const EXTRACTION_HINT =
  "Работы для извлечения, не больше 8. Отметка работы без решения записывает её как соответствующую критериям.";

export const VERSION_TYPE: Record<Row["version_type"], string> = {
  journal: "Журнальная версия",
  preprint: "Препринт",
};

export const provenanceLabel = (row: Pick<Row, "provenance">) =>
  row.provenance.map((p) => PROVENANCE_LABEL[p]).join(" + ");

/** Открытый текст по сведениям Europe PMC; документ загружают на шаге «Подготовка». */
export const OPEN_ACCESS: Record<Row["open_access"], { label: string; tone: Tone }> = {
  open: { label: "Есть", tone: "success" },
  closed: { label: "Нет", tone: "neutral" },
  unknown: { label: "Нет данных", tone: "neutral" },
};
