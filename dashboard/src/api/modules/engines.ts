import { request } from "../request";

export interface EngineSessionSummary {
  id: string;
  cwd: string;
  state: string;
}

export interface EngineSession extends EngineSessionSummary {
  engine: string;
  expert: string;
  engine_version: string;
  error?: string | null;
  messages: { role: string; text: string; tools?: unknown[] }[];
  permission?: {
    title: string;
    options: { option_id?: string; optionId?: string; name: string }[];
  } | null;
}

const base = "/engines/dsh/sessions";
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export const enginesApi = {
  list: () => request<EngineSessionSummary[]>(base),
  create: (cwd: string) =>
    request<EngineSession>(base, {
      method: "POST",
      body: JSON.stringify({ cwd }),
    }),
  get: (id: string) => request<EngineSession>(path(id)),
  prompt: (id: string, text: string) =>
    request<EngineSession>(`${path(id)}/prompt`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  permission: (id: string, option_id: string) =>
    request<EngineSession>(`${path(id)}/permission`, {
      method: "POST",
      body: JSON.stringify({ option_id }),
    }),
  cancel: (id: string) =>
    request<EngineSession>(`${path(id)}/cancel`, { method: "POST" }),
};
