import type { components } from "./schema";

export type Schemas = components["schemas"];

type Fields = Schemas["CriteriaFields"];
export type Origin = Schemas["SearchCriterion"]["origin"];
export type ArticleType = Fields["article_types"][number];
export type PublicationKind = Fields["publication_kinds"][number];
export type Condition = Fields["conditions"][number];
export type Resource = Fields["resources"][number];
export type Decision = Schemas["PublicationRow"]["decision"];
export type SourceStatus = Schemas["SourceRun"]["status"];
