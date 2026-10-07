import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const api = createClient<paths>();

export const isMockApi = import.meta.env.VITE_API_MOCK === "true";
