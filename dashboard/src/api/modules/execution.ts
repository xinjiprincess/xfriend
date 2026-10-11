import { request } from "../request";

export interface ExecutionExpert {
  id: string;
  name: string;
  engine: "native" | "dsh";
  workspace: string;
  capabilities: string[];
}
export interface TaskStep {
  expert_id: string;
  name: string;
  engine: string;
  workspace: string;
  session_id: string;
  engine_session_id?: string;
  engine_version?: string;
}
export interface ExecutionTask {
  id: string;
  state: string;
  created_at: number;
  updated_at: number;
  steps: TaskStep[];
  step: number;
  error?: string;
  messages: {
    role: string;
    content: string;
    engine?: string;
    expert_id?: string;
  }[];
  events: {
    id: string;
    sequence: number;
    step: number;
    type: string;
    content?: string;
    detail?: unknown;
  }[];
  permission?: {
    id: string;
    request: {
      options?: { option_id: string; name: string }[];
      request?: unknown;
    };
  };
}
export type ExecutionTaskSummary = Omit<
  ExecutionTask,
  "messages" | "events" | "permission"
>;
export const executionApi = {
  experts: () => request<ExecutionExpert[]>("/execution/experts"),
  bind: (id: string, engine: string, workspace: string) =>
    request<ExecutionExpert>(`/execution/experts/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ engine, workspace }),
    }),
  list: () => request<ExecutionTaskSummary[]>("/execution/tasks"),
  get: (id: string) => request<ExecutionTask>(`/execution/tasks/${id}`),
  create: (expert_ids: string[], text: string) =>
    request<ExecutionTask>("/execution/tasks", {
      method: "POST",
      body: JSON.stringify({ expert_ids, text }),
    }),
  input: (id: string, text: string) =>
    request<ExecutionTask>(`/execution/tasks/${id}/input`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  cancel: (id: string) =>
    request<ExecutionTask>(`/execution/tasks/${id}/cancel`, { method: "POST" }),
  answer: (
    id: string,
    permission_id: string,
    answer: { decision?: string; option_id?: string; message?: string },
  ) =>
    request<ExecutionTask>(`/execution/tasks/${id}/permission`, {
      method: "POST",
      body: JSON.stringify({ permission_id, ...answer }),
    }),
  assistant: () =>
    request<{ expert_ids: string[]; wake_word: string }>(
      "/execution/assistant",
    ),
  setAssistant: (expert_ids: string[], wake_word?: string) =>
    request<{ expert_ids: string[]; wake_word: string }>(
      "/execution/assistant",
      {
        method: "PUT",
        body: JSON.stringify({
          expert_ids,
          ...(wake_word ? { wake_word } : {}),
        }),
      },
    ),
};
