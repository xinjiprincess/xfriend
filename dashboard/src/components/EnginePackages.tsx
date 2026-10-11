import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Input, List, Popconfirm, Space, Tag } from "antd";
import { useTranslation } from "react-i18next";
import {
  enginePackagesApi,
  type EnginePackage,
} from "../api/modules/enginePackages";

export default function EnginePackages() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [items, setItems] = useState<EnginePackage[]>([]);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(
    async () => setItems(await enginePackagesApi.list()),
    [],
  );
  useEffect(() => {
    void load().catch(() => {});
  }, [load]);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      await load();
      message.success(t("enginePackages.saved"));
    } catch {
      message.error(t("enginePackages.failed"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={t("enginePackages.title")}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <p>{t("enginePackages.hint")}</p>
        <Input
          value={path}
          onChange={(event) => setPath(event.target.value)}
          placeholder={t("enginePackages.path")}
        />
        <Button
          disabled={busy || !path.trim()}
          onClick={() => void run(() => enginePackagesApi.install(path))}
        >
          {t("enginePackages.import")}
        </Button>
        <List
          dataSource={items}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="activate"
                  title={t("enginePackages.confirm")}
                  onConfirm={() =>
                    run(() => enginePackagesApi.activate(item.id))
                  }
                >
                  <Button disabled={busy || item.active}>
                    {t("enginePackages.activate")}
                  </Button>
                </Popconfirm>,
              ]}
            >
              <Space>
                <strong>{item.version}</strong>
                <code>{item.upstream_commit.slice(0, 12)}</code>
                {item.active && (
                  <Tag color="green">{t("enginePackages.active")}</Tag>
                )}
              </Space>
            </List.Item>
          )}
        />
      </Space>
    </Card>
  );
}
