import { request } from "../request";
export interface ExpertTemplate {
  id: string;
  plugin_id: string;
  plugin_version: string;
  name: string;
  description: string;
  engine: string;
  system_prompt: string;
  permissions: string[];
  dependencies: string[];
}
export const executionTemplatesApi = {
  list: () => request<ExpertTemplate[]>("/execution/templates"),
  create: (
    plugin_id: string,
    template_id: string,
    name: string,
    workspace: string,
  ) =>
    request<Record<string, string>>("/execution/templates", {
      method: "POST",
      body: JSON.stringify({ plugin_id, template_id, name, workspace }),
    }),
};
