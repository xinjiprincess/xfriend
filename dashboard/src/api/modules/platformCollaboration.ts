import { request } from "../request";

export interface PlatformAccount {
  origin: string;
  logged_in: boolean;
  user: Record<string, unknown> | null;
  tenants: { id: number; name: string }[];
}
export interface PlatformDevice {
  id: number | string;
  name?: string;
  description?: string;
  isActive?: boolean | number;
}
const base = "/platform-collaboration";
export const platformApi = {
  status: () => request<PlatformAccount>(base),
  login: (values: { origin: string; username: string; password: string }) =>
    request<PlatformAccount>(`${base}/login`, {
      method: "POST",
      body: JSON.stringify(values),
    }),
  tenant: (tenant_id: number) =>
    request<PlatformAccount>(`${base}/tenant`, {
      method: "POST",
      body: JSON.stringify({ tenant_id }),
    }),
  devices: () => request<PlatformDevice[]>(`${base}/devices`),
  logout: () => request<PlatformAccount>(`${base}/logout`, { method: "POST" }),
};
