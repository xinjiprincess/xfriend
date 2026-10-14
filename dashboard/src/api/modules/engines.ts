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
  extensions?: string[];
  messages: { role: string; text: string; tools?: unknown[] }[];
  permission?: {
    title: string;
    options: { option_id?: string; optionId?: string; name: string }[];
  } | null;
}

const base = "/engines/dsh/sessions";
const path = (id: string) => `${base}/${encodeURIComponent(id)}`;
export const enginesApi = {
  expert: () => request<DSHExpertView>("/engines/dsh/expert"),
  setExpert: (cwd: string, connectors: string[], model: string) =>
    request<DSHExpertView>("/engines/dsh/expert", {
      method: "PUT",
      body: JSON.stringify({ cwd, connectors, model }),
    }),
  web: () => request<{ url: string }>("/engines/dsh/web"),
  extensions: () =>
    request<{ extensions: MCPExtension[] }>("/engines/dsh/extensions"),
  setExtensions: (extensions: MCPExtension[]) =>
    request<{ extensions: MCPExtension[] }>("/engines/dsh/extensions", {
      method: "PUT",
      body: JSON.stringify({ extensions }),
    }),
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

export interface MCPExtension {
  name: string;
  transport: "stdio" | "http";
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
}

export interface DSHExpertView {
  id: string;
  engine: string;
  available: boolean;
  cwd: string;
  connectors: string[];
  model: string;
  models: { id: string; name: string }[];
  choices: { id: string; name: string; status: string }[];
}
