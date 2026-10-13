import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Collapse,
  Form,
  Input,
  Space,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import { acpApi } from "../api/modules/acp";
import { enginesApi, type MCPExtension } from "../api/modules/engines";
import type { ACPRunnerConfig } from "../api/types/acp";
import {
  ACPDrawer,
  formValuesToRunner,
  runnerToFormValues,
} from "../pages/Agent/ACP/components/ACPDrawer";

export default function DSHSettings() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [runner, setRunner] = useState<ACPRunnerConfig>();
  const [extensions, setExtensions] = useState("[]");
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  useEffect(() => {
    let disposed = false;
    void Promise.all([acpApi.getRunner("dsh"), enginesApi.extensions()])
      .then(([config, plugins]) => {
        if (disposed) return;
        setRunner(config);
        setExtensions(JSON.stringify(plugins.extensions, null, 2));
        setLoaded(true);
      })
      .catch(() => {
        if (!disposed) message.error(t("engines.failed"));
      });
    return () => {
      disposed = true;
    };
  }, [message, t]);
  const saveExtensions = async () => {
    setBusy(true);
    try {
      const parsed: unknown = JSON.parse(extensions);
      if (!Array.isArray(parsed)) throw new Error("Invalid extensions");
      const saved = await enginesApi.setExtensions(parsed as MCPExtension[]);
      setExtensions(JSON.stringify(saved.extensions, null, 2));
      message.success(t("execution.saved"));
    } catch {
      message.error(t("engineHub.extensionInvalid"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {loaded && !runner?.enabled && (
        <Alert
          type="warning"
          showIcon
          message={t("engineHub.runnerDisabled")}
        />
      )}
      <Space wrap>
        <Button
          disabled={!runner || busy}
          onClick={() => {
            form.setFieldsValue(runnerToFormValues("dsh", runner!));
            setOpen(true);
          }}
        >
          {t("engineHub.configureRunner")}
        </Button>
      </Space>
      <Collapse
        items={[
          {
            key: "mcp",
            label: t("engineHub.extensions"),
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Typography.Paragraph>
                  {t("engineHub.extensionHint")}
                </Typography.Paragraph>
                <Typography.Paragraph type="secondary">
                  {t("engineHub.extensionExample")}
                </Typography.Paragraph>
                <Input.TextArea
                  disabled={!loaded || busy}
                  value={extensions}
                  rows={8}
                  spellCheck={false}
                  aria-label={t("engineHub.extensions")}
                  onChange={(event) => setExtensions(event.target.value)}
                />
                <Button
                  disabled={!loaded}
                  loading={busy}
                  onClick={() => void saveExtensions()}
                >
                  {t("engineHub.saveExtensions")}
                </Button>
              </Space>
            ),
          },
        ]}
      />
      <ACPDrawer
        open={open}
        activeKey="dsh"
        isCreateMode={false}
        form={form}
        saving={busy}
        canEditKey={false}
        canDelete={false}
        onClose={() => setOpen(false)}
        onDelete={() => undefined}
        onSubmit={async (values) => {
          setBusy(true);
          try {
            setRunner(
              await acpApi.updateRunner("dsh", formValuesToRunner(values)),
            );
            setOpen(false);
            message.success(t("execution.saved"));
          } catch {
            message.error(t("engines.failed"));
          } finally {
            setBusy(false);
          }
        }}
      />
    </>
  );
}
