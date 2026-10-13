import { request } from "../request";

export interface StartupDiagnostics {
  startup_id: string;
  phase: string;
  ready: boolean;
  elapsed_ms: number;
  checkpoints: Record<string, number>;
  failure: string | null;
}

export interface RuntimeReadiness {
  ready: boolean;
  core_ready: boolean;
  runtime_ready: boolean;
  runtime_failed: boolean;
  agents_ready: boolean;
}

export const healthApi = {
  readiness: (signal?: AbortSignal) =>
    request<RuntimeReadiness>("/health", { signal }),
  startup: () => request<StartupDiagnostics>("/health/startup"),
};
