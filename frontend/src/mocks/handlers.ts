import { HttpResponse, http } from "msw";
import type { components } from "../api/schema";
import { extractionHandlers } from "./extraction";
import { resultHandlers } from "./result";
import { searchHandlers } from "./search";
import { studyHandlers } from "./study";

type Schemas = components["schemas"];

/** Демо-режим начинается с выполненного входа; выход и вход работают в пределах вкладки. */
let signedIn: Schemas["SessionResponse"] | null = { login: "demo.researcher" };

export const handlers = [
  http.get("/api/health", () =>
    HttpResponse.json<Schemas["HealthResponse"]>({ status: "ok", database: "ok" }),
  ),
  http.get("/api/session", () =>
    signedIn
      ? HttpResponse.json<Schemas["SessionResponse"]>(signedIn)
      : HttpResponse.json({ detail: "Требуется вход" }, { status: 401 }),
  ),
  http.post("/api/session", async ({ request }) => {
    const { login } = (await request.json()) as Schemas["Credentials"];
    signedIn = { login };
    return HttpResponse.json<Schemas["SessionResponse"]>(signedIn);
  }),
  http.delete("/api/session", () => {
    signedIn = null;
    return new HttpResponse(null, { status: 204 });
  }),
  ...studyHandlers,
  ...searchHandlers,
  ...extractionHandlers,
  ...resultHandlers,
];
