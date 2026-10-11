import { request } from "../request";

export interface StartupDiagnostics {
  startup_id: string;
  phase: string;
  ready: boolean;
  elapsed_ms: number;
  checkpoints: Record<string, number>;
  failure: string | null;
}

export const healthApi = {
  startup: () => request<StartupDiagnostics>("/health/startup"),
};
