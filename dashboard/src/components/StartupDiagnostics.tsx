import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Space,
  Table,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import { authApi } from "../api/modules/auth";
import {
  healthApi,
  type StartupDiagnostics as Snapshot,
} from "../api/modules/health";

export default function StartupDiagnostics() {
  const { t } = useTranslation();
  const [admin, setAdmin] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let disposed = false;
    void authApi
      .me()
      .then(async (user) => {
        if (user.role !== "admin" || disposed) return;
        setAdmin(true);
        const value = await healthApi.startup();
        if (!disposed) setSnapshot(value);
      })
      .catch(() => {
        if (!disposed) setFailed(true);
      });
    return () => {
      disposed = true;
    };
  }, []);
  if (!admin) return null;
  const refresh = async () => {
    setBusy(true);
    try {
      setSnapshot(await healthApi.startup());
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card
      title={t("startupDiagnostics.title")}
      extra={
        <Button loading={busy} onClick={() => void refresh()}>
          {t("startupDiagnostics.refresh")}
        </Button>
      }
    >
      <Space direction="vertical" style={{ width: "100%" }}>
        {failed && (
          <Alert type="error" message={t("startupDiagnostics.failed")} />
        )}
        {snapshot && (
          <>
            <Descriptions
              size="small"
              column={1}
              items={[
                {
                  key: "id",
                  label: t("startupDiagnostics.id"),
                  children: (
                    <Typography.Text copyable>
                      {snapshot.startup_id}
                    </Typography.Text>
                  ),
                },
                {
                  key: "ready",
                  label: t("startupDiagnostics.status"),
                  children: t(
                    snapshot.ready
                      ? "startupDiagnostics.ready"
                      : "startupDiagnostics.notReady",
                  ),
                },
                {
                  key: "total",
                  label: t("startupDiagnostics.total"),
                  children: `${(snapshot.elapsed_ms / 1000).toFixed(2)} s`,
                },
              ]}
            />
            {snapshot.failure && (
              <Alert type="error" message={snapshot.failure} />
            )}
            <Table
              size="small"
              pagination={false}
              rowKey="phase"
              dataSource={Object.entries(snapshot.checkpoints).map(
                ([phase, elapsed], index, entries) => ({
                  phase,
                  elapsed,
                  duration: elapsed - (index ? entries[index - 1][1] : 0),
                }),
              )}
              columns={[
                {
                  title: t("startupDiagnostics.phase"),
                  dataIndex: "phase",
                  render: (phase: string) =>
                    t(`startupDiagnostics.phases.${phase}`, {
                      defaultValue: phase,
                    }),
                },
                {
                  title: t("startupDiagnostics.duration"),
                  dataIndex: "duration",
                  render: (value: number) => `${value} ms`,
                },
                {
                  title: t("startupDiagnostics.elapsed"),
                  dataIndex: "elapsed",
                  render: (value: number) => `${value} ms`,
                },
              ]}
            />
            <Typography.Paragraph type="secondary">
              {t("startupDiagnostics.scope")}
            </Typography.Paragraph>
          </>
        )}
      </Space>
    </Card>
  );
}
