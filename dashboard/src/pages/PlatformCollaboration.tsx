import { useEffect, useState } from "react";
import { Alert, App, Button, Form, Input, Select, Space, Table } from "antd";
import { useTranslation } from "react-i18next";
import PageShell from "../layouts/PageShell";
import {
  platformApi,
  type PlatformAccount,
  type PlatformDevice,
} from "../api/modules/platformCollaboration";

export default function PlatformCollaboration() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [account, setAccount] = useState<PlatformAccount>();
  const [devices, setDevices] = useState<PlatformDevice[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form] = Form.useForm();
  const update = async (next: PlatformAccount) => {
    setAccount(next);
    form.setFieldValue("origin", next.origin);
    setDevices(next.logged_in ? await platformApi.devices() : []);
  };
  const run = async (action: () => Promise<PlatformAccount>) => {
    setBusy(true);
    setError("");
    try {
      await update(await action());
    } catch {
      setError(t("platformCollaboration.failed"));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let active = true;
    platformApi
      .status()
      .then(async (next) => {
        if (!active) return;
        setAccount(next);
        form.setFieldValue("origin", next.origin);
        if (next.logged_in) {
          const rows = await platformApi.devices();
          if (active) setDevices(rows);
        }
      })
      .catch(() => {
        if (active) setError(t("platformCollaboration.failed"));
      });
    return () => {
      active = false;
    };
  }, [form, t]);
  return (
    <PageShell
      title={t("platformCollaboration.title")}
      subtitle={t("platformCollaboration.subtitle")}
    >
      <Space direction="vertical" style={{ width: "100%" }} size="large">
        <Alert
          type="info"
          showIcon
          message={t("platformCollaboration.scope")}
        />
        {error && <Alert type="error" showIcon message={error} />}
        {!account?.logged_in && (
          <Form
            form={form}
            layout="vertical"
            initialValues={{ origin: "https://www.ciyuancenter.cc" }}
            onFinish={(values) => {
              void run(async () => {
                try {
                  return await platformApi.login(values);
                } finally {
                  form.setFieldValue("password", "");
                }
              });
            }}
          >
            <Form.Item
              name="origin"
              label={t("platformCollaboration.origin")}
              rules={[{ required: true }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              name="username"
              label={t("platformCollaboration.username")}
              rules={[{ required: true }]}
            >
              <Input autoComplete="username" />
            </Form.Item>
            <Form.Item
              name="password"
              label={t("platformCollaboration.password")}
              rules={[{ required: true }]}
            >
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={busy}>
              {t("platformCollaboration.connect")}
            </Button>
          </Form>
        )}
        {!!account?.tenants.length && (
          <Select
            style={{ minWidth: 240 }}
            placeholder={t("platformCollaboration.tenant")}
            options={account.tenants.map((row) => ({
              value: row.id,
              label: row.name,
            }))}
            onChange={(id) => void run(() => platformApi.tenant(id))}
            disabled={busy}
          />
        )}
        {account?.logged_in && (
          <>
            <Space>
              <Button
                loading={busy}
                onClick={() => void run(() => platformApi.status())}
              >
                {t("platformCollaboration.refresh")}
              </Button>
              <Button onClick={() => void run(() => platformApi.logout())}>
                {t("platformCollaboration.disconnect")}
              </Button>
              <Button
                onClick={() => {
                  window.open(account.origin, "_blank", "noopener,noreferrer");
                  void message.info(t("platformCollaboration.opened"));
                }}
              >
                {t("platformCollaboration.portal")}
              </Button>
            </Space>
            <Table
              rowKey="id"
              dataSource={devices}
              columns={[
                { title: t("platformCollaboration.device"), dataIndex: "name" },
                {
                  title: t("platformCollaboration.description"),
                  dataIndex: "description",
                },
                {
                  title: t("platformCollaboration.enabled"),
                  dataIndex: "isActive",
                  render: (value: unknown) =>
                    value === true || value === 1
                      ? t("platformCollaboration.yes")
                      : t("platformCollaboration.no"),
                },
              ]}
            />
          </>
        )}
      </Space>
    </PageShell>
  );
}
