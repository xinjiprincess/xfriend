import { request } from "../request";
export interface EnginePackage {
  id: string;
  version: string;
  upstream_commit: string;
  active: boolean;
}
export const enginePackagesApi = {
  list: () => request<EnginePackage[]>("/engines/dsh/packages"),
  install: (path: string) =>
    request<{ id: string }>("/engines/dsh/packages", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  activate: (id: string) =>
    request<{ id: string }>(`/engines/dsh/packages/${id}/activate`, {
      method: "POST",
    }),
};
