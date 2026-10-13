import { request } from "../request";

export interface ProductLicense {
  required: boolean;
  valid: boolean;
  state: string;
  device_id: string;
  edition: "trial" | "full" | null;
  expires_at: number | null;
  last_synced_at: number | null;
  sync_error: string | null;
  timezone: string;
  mode?: "development" | "server" | "local_trial";
  trial_days?: number | null;
}

export const licensingApi = {
  status: () => request<ProductLicense>("/licensing/status"),
  sync: () => request<ProductLicense>("/licensing/sync", { method: "POST" }),
  activate: (code?: string) =>
    request<ProductLicense>("/licensing/activate", {
      method: "POST",
      body: JSON.stringify({ code: code?.trim() || null }),
    }),
};
