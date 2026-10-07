import type { Schemas } from "./types";

type Provenance = Schemas["PublicationRow"]["provenance"][number];

/** Подписи источников записей, общие для поиска, отбора и карточки. */
export const PROVENANCE_LABEL: Record<Provenance, string> = {
  pubmed: "PubMed",
  europe_pmc: "Europe PMC",
  manual: "Добавлено вручную",
};
