import { request, requestBlob } from "../request";

export interface ExportMessage {
  role: string;
  content: unknown;
  agent_id?: string;
}
export interface DocumentSelection {
  format: "html" | "pdf" | "docx" | "png";
  mode: "all" | "turn" | "selected" | "range";
  indices: number[];
  start: number;
  end: number;
  digest: string;
}
const base = (agent: string, thread: string, engine?: "dsh" | "execution") =>
  engine === "execution"
    ? `/execution/tasks/${encodeURIComponent(thread)}`
    : engine === "dsh"
    ? `/engines/dsh/sessions/${encodeURIComponent(thread)}`
    : `/agents/${encodeURIComponent(agent)}/threads/${encodeURIComponent(
        thread,
      )}`;
export const conversationDocumentsApi = {
  preview: (agent: string, thread: string, engine?: "dsh" | "execution") =>
    request<{ digest: string; messages: ExportMessage[] }>(
      `${base(agent, thread, engine)}/document-preview`,
    ),
  export: (
    agent: string,
    thread: string,
    selection: DocumentSelection,
    engine?: "dsh" | "execution",
  ) =>
    requestBlob(`${base(agent, thread, engine)}/document`, {
      method: "POST",
      body: JSON.stringify(selection),
      headers: { "Content-Type": "application/json" },
    }),
};
