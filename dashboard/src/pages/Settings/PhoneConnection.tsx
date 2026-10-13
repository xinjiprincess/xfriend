import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Radio,
  Space,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  phoneConnectionApi,
  type PhoneConnection,
  type PhoneConnectionOptions,
} from "../../api/modules/phoneConnection";
import { usesCloudAccount } from "../../api/modules/cloudAccount";

export default function PhoneConnectionPage() {
  const { t } = useTranslation();
  const [state, setState] = useState<PhoneConnection>({ enabled: false });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [usbReady, setUsbReady] = useState(false);
  const [form] = Form.useForm<PhoneConnectionOptions>();
  const mode = Form.useWatch("mode", form);
  const supported =
    document.documentElement.dataset.xfriendMobile !== "1" &&
    usesCloudAccount() &&
    document.documentElement.dataset.xfriendDesktop === "1";
  useEffect(() => {
    if (!supported) return;
    let active = true;
    const refresh = () =>
      phoneConnectionApi
        .status()
        .then((value) => {
          if (active) setState(value);
        })
        .catch((e) => {
          if (active) setError(String(e.message));
        });
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [supported]);
  async function start(value: PhoneConnectionOptions) {
    setBusy(true);
    setError("");
    setUsbReady(false);
    try {
      setState(await phoneConnectionApi.start(value));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function action(usb: boolean) {
    setBusy(true);
    setError("");
    try {
      if (usb) {
        await phoneConnectionApi.usb();
        setUsbReady(true);
      } else {
        setState(await phoneConnectionApi.stop());
        setUsbReady(false);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Space
      direction="vertical"
      size="large"
      style={{ width: "100%", maxWidth: 800 }}
    >
      <Typography.Title level={3}>
        {t("phoneConnection.title")}
      </Typography.Title>
      <Typography.Paragraph>
        {t("phoneConnection.description")}
      </Typography.Paragraph>
      {!supported ? (
        <Alert type="info" message={t("phoneConnection.desktopOnly")} />
      ) : (
        <>
          {error && (
            <Alert
              type="error"
              showIcon
              message={error}
              closable
              onClose={() => setError("")}
            />
          )}
          {state.enabled ? (
            <Card title={t("phoneConnection.active")}>
              <Typography.Paragraph>
                {t("phoneConnection.address")}:{" "}
                <Typography.Text copyable>{state.address}</Typography.Text>
              </Typography.Paragraph>
              <Typography.Paragraph>
                {t("phoneConnection.code")}:{" "}
                <Typography.Text copyable strong>
                  {state.code}
                </Typography.Text>
              </Typography.Paragraph>
              <Typography.Paragraph>
                {t("phoneConnection.connections", {
                  count: state.connections || 0,
                })}
              </Typography.Paragraph>
              <Alert type="info" message={t("phoneConnection.pairHelp")} />
              {state.mode === "usb" && (
                <Typography.Paragraph style={{ marginTop: 16 }}>
                  {t("phoneConnection.usbHelp")}{" "}
                  <Typography.Text code>
                    adb reverse tcp:{state.port} tcp:{state.port}
                  </Typography.Text>
                </Typography.Paragraph>
              )}
              {usbReady && (
                <Alert type="success" message={t("phoneConnection.usbReady")} />
              )}
              <Space style={{ marginTop: 16 }}>
                {state.mode === "usb" && (
                  <Button loading={busy} onClick={() => void action(true)}>
                    {t("phoneConnection.usbSetup")}
                  </Button>
                )}
                <Button
                  danger
                  loading={busy}
                  onClick={() => void action(false)}
                >
                  {t("phoneConnection.stop")}
                </Button>
              </Space>
            </Card>
          ) : (
            <Card>
              <Form
                form={form}
                layout="vertical"
                initialValues={{ mode: "usb", port: 8765 }}
                onFinish={start}
              >
                <Form.Item name="mode" label={t("phoneConnection.mode")}>
                  <Radio.Group
                    options={[
                      { value: "usb", label: "USB" },
                      { value: "https", label: "HTTPS" },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  name="port"
                  label={t("phoneConnection.port")}
                  rules={[{ required: true }]}
                >
                  <InputNumber min={1024} max={65535} />
                </Form.Item>
                {mode === "https" && (
                  <>
                    <Alert
                      type="info"
                      message={t("phoneConnection.httpsHelp")}
                    />
                    <Form.Item
                      name="origin"
                      label={t("phoneConnection.address")}
                      rules={[
                        { required: true },
                        {
                          pattern: /^https:\/\/[^/]+$/,
                          message: t("phoneConnection.httpsAddress"),
                        },
                      ]}
                    >
                      <Input placeholder="https://pc.example.com:8765" />
                    </Form.Item>
                    <Form.Item
                      name="certificate"
                      label={t("phoneConnection.certificate")}
                      rules={[{ required: true }]}
                    >
                      <Input />
                    </Form.Item>
                    <Form.Item
                      name="key"
                      label={t("phoneConnection.key")}
                      rules={[{ required: true }]}
                    >
                      <Input />
                    </Form.Item>
                  </>
                )}
                <Button type="primary" htmlType="submit" loading={busy}>
                  {t("phoneConnection.start")}
                </Button>
              </Form>
            </Card>
          )}
        </>
      )}
      <Alert type="info" message={t("phoneConnection.boundary")} />
    </Space>
  );
}
