import { useEffect, useState } from "react";
import { App, Button, Card, Input, Select, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import {
  executionTemplatesApi,
  type ExpertTemplate,
} from "../api/modules/executionTemplates";
export default function ExpertTemplates({
  onCreated,
}: {
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [items, setItems] = useState<ExpertTemplate[]>([]);
  const [selected, setSelected] = useState<string>();
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void executionTemplatesApi
      .list()
      .then(setItems)
      .catch(() => {});
  }, []);
  const template = items.find(
    (item) => `${item.plugin_id}:${item.id}` === selected,
  );
  if (!items.length) return null;
  return (
    <Card title={t("expertTemplates.title")} style={{ marginBottom: 16 }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Select
          style={{ width: "100%" }}
          value={selected}
          options={items.map((item) => ({
            value: `${item.plugin_id}:${item.id}`,
            label: `${item.name} · ${item.engine} · ${item.plugin_version}`,
          }))}
          onChange={(value) => {
            setSelected(value);
            setName(
              items.find((item) => `${item.plugin_id}:${item.id}` === value)
                ?.name || "",
            );
          }}
        />
        {template && (
          <>
            <Typography.Paragraph>{template.description}</Typography.Paragraph>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                maxHeight: 180,
                overflow: "auto",
              }}
            >
              {template.system_prompt}
            </pre>
            <Typography.Text>
              {t("expertTemplates.permissions")}:{" "}
              {template.permissions.join(", ") || "—"}
            </Typography.Text>
            <Input
              value={name}
              maxLength={100}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("expertTemplates.name")}
            />
            {template.engine === "dsh" && (
              <Input
                value={workspace}
                onChange={(event) => setWorkspace(event.target.value)}
                placeholder={t("execution.workspace")}
              />
            )}
            <Button
              disabled={busy || !name.trim()}
              onClick={async () => {
                setBusy(true);
                try {
                  await executionTemplatesApi.create(
                    template.plugin_id,
                    template.id,
                    name,
                    workspace,
                  );
                  onCreated();
                  message.success(t("expertTemplates.created"));
                } catch {
                  message.error(t("execution.failed"));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t("expertTemplates.create")}
            </Button>
          </>
        )}
      </Space>
    </Card>
  );
}
