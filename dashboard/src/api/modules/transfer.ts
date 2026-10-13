import { request, requestBlob, requestUpload } from "../request";

export interface TransferAgent {
  id: string;
  name: string;
  kind: "expert" | "team";
}
export interface TransferProvider {
  id: number;
  name: string;
  kind: string;
  family: "text" | "voice";
  models?: string[];
}
export interface TransferInventory {
  agents: TransferAgent[];
  providers: TransferProvider[];
  voice_providers: TransferProvider[];
  can_manage_models: boolean;
}
export interface PortableProvider {
  ref: string;
  name: string;
  family: "text" | "voice";
  kind: string;
  has_secret: boolean;
}
export interface PortableExpert {
  ref: string;
  name: string;
  kind: "expert" | "team";
  engine: "native" | "dsh";
  dependencies: string[];
  member_refs: string[];
  files: Record<string, string>;
}
export interface Inspection {
  id: string;
  package: {
    client_version: string;
    experts: PortableExpert[];
    providers: PortableProvider[];
    warnings: string[];
  };
}
export interface ImportChoice {
  action: "create" | "reuse" | "skip";
  target?: string;
}
export interface ImportResult {
  state: string;
  mapping: Record<string, string>;
  pending: { ref: string; reason: string }[];
  created_agents: string[];
  created_providers: number;
}
export interface TransferPreviewFile {
  expert: string;
  path: string;
  size: number;
  sample: string;
  possible_secret: boolean;
}
export interface TransferJob {
  id: string;
  state: string;
  message?: string;
  warnings?: string[];
  preview?: TransferPreviewFile[];
}

export const transferApi = {
  inventory: () => request<TransferInventory>("/transfer/inventory"),
  export: (body: {
    agent_ids: string[];
    provider_ids: number[];
    voice_provider_ids: number[];
    include_skills: boolean;
    include_secrets: boolean;
    password: string;
  }) =>
    request<TransferJob>("/transfer/exports", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  job: (id: string) =>
    request<TransferJob>(`/transfer/jobs/${encodeURIComponent(id)}`),
  download: (id: string) =>
    requestBlob(`/transfer/exports/${encodeURIComponent(id)}/file`),
  inspect: (file: File, password: string) => {
    const body = new FormData();
    body.append("file", file);
    body.append("password", password);
    return requestUpload<Inspection>("/transfer/imports/inspect", body);
  },
  plan: (
    id: string,
    choices: Record<string, ImportChoice>,
    trust_skills: boolean,
  ) =>
    request<{ revision: string }>(
      `/transfer/imports/${encodeURIComponent(id)}/plan`,
      { method: "POST", body: JSON.stringify({ choices, trust_skills }) },
    ),
  commit: (id: string, revision: string, password: string) =>
    request<ImportResult>(
      `/transfer/imports/${encodeURIComponent(id)}/commit`,
      { method: "POST", body: JSON.stringify({ revision, password }) },
    ),
  cancel: (id: string) =>
    request<void>(`/transfer/imports/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
};
