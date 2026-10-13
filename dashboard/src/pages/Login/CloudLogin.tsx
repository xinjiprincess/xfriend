import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  Space,
  Typography,
} from "antd";
import { cloudAccountApi } from "../../api/modules/cloudAccount";
import { useCloudAccount } from "../../context/CloudAccountContext";

export default function CloudLogin() {
  const { t } = useTranslation();
  const account = useCloudAccount();
  const navigate = useNavigate();
  const registration = useLocation().pathname === "/register";
  const [help, setHelp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [remember, setRemember] = useState(
    account?.status?.remember_account ?? true,
  );
  const [keep, setKeep] = useState(account?.status?.keep_login ?? true);
  const [form] = Form.useForm();
  const submit = async (values: Record<string, string>) => {
    setBusy(true);
    setError("");
    try {
      const options = { remember_account: remember, keep_login: keep };
      const result = registration
        ? await cloudAccountApi.register({
            phone: values.phone,
            email: values.email,
            name: values.name,
            password: values.password,
            ...options,
          })
        : await cloudAccountApi.login({
            identifier: values.identifier,
            password: values.password,
            ...options,
          });
      form.setFieldValue("password", "");
      account?.update(result);
      navigate("/chat", { replace: true });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : t("cloudAccount.loginFailed"),
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        padding: 24,
        background: "var(--fn-bg-layout)",
      }}
    >
      <Card style={{ width: "100%", maxWidth: 460 }}>
        <Space align="center">
          <img src="/xfriend-icon.png" width={52} alt="" />
          <Typography.Title level={3} style={{ margin: 0 }}>
            Singu Xfriend
          </Typography.Title>
        </Space>
        <Typography.Title level={4}>
          {registration ? t("cloudAccount.register") : t("cloudAccount.login")}
        </Typography.Title>
        {error && (
          <Alert
            type="error"
            showIcon
            message={error}
            style={{ marginBottom: 16 }}
          />
        )}
        {account?.status?.message && !error && (
          <Alert
            type="info"
            message={account.status.message}
            style={{ marginBottom: 16 }}
          />
        )}
        {help && (
          <Alert
            type="info"
            showIcon
            message={t("cloudAccount.resetHelp")}
            style={{ marginBottom: 16 }}
          />
        )}
        <Form
          form={form}
          layout="vertical"
          onFinish={submit}
          initialValues={{ identifier: account?.status?.identifier || "" }}
        >
          {registration && (
            <>
              <Form.Item
                name="name"
                label={t("cloudAccount.name")}
                rules={[{ required: true, whitespace: true }]}
              >
                <Input maxLength={100} autoComplete="nickname" />
              </Form.Item>
              <Form.Item
                name="email"
                label={t("cloudAccount.email")}
                rules={[{ required: true, type: "email" }]}
              >
                <Input maxLength={254} autoComplete="email" />
              </Form.Item>
            </>
          )}
          {registration ? (
            <Form.Item
              name="phone"
              label={t("cloudAccount.phone")}
              rules={[{ required: true }]}
            >
              <Input
                maxLength={20}
                autoComplete="tel"
                placeholder={t("cloudAccount.phoneHint")}
              />
            </Form.Item>
          ) : (
            <Form.Item
              name="identifier"
              label={t("cloudAccount.identifier")}
              rules={[{ required: true }]}
            >
              <Input autoComplete="username" maxLength={254} />
            </Form.Item>
          )}
          <Form.Item
            name="password"
            label={t("cloudAccount.password")}
            rules={[{ required: true }]}
          >
            <Input.Password
              autoComplete={registration ? "new-password" : "current-password"}
              maxLength={256}
            />
          </Form.Item>
          {registration && (
            <Form.Item
              name="confirm"
              label={t("cloudAccount.confirm")}
              dependencies={["password"]}
              rules={[
                { required: true },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    return value === getFieldValue("password")
                      ? Promise.resolve()
                      : Promise.reject(new Error(t("cloudAccount.mismatch")));
                  },
                }),
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
          )}
          {
            <Space wrap style={{ marginBottom: 20 }}>
              <Checkbox
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              >
                {t("cloudAccount.remember")}
              </Checkbox>
              <Checkbox
                checked={keep}
                onChange={(e) => setKeep(e.target.checked)}
              >
                {t("cloudAccount.keep")}
              </Checkbox>
            </Space>
          }
          <Button type="primary" htmlType="submit" block loading={busy}>
            {registration
              ? t("cloudAccount.registerSubmit")
              : t("cloudAccount.login")}
          </Button>
        </Form>
        <Space wrap style={{ marginTop: 16 }}>
          <Button
            type="link"
            onClick={() => {
              setHelp(false);
              setError("");
              form.resetFields();
              navigate(registration ? "/login" : "/register");
            }}
          >
            {registration
              ? t("cloudAccount.existing")
              : t("cloudAccount.newAccount")}
          </Button>
          {!registration && (
            <Button
              type="link"
              onClick={() => {
                setHelp(true);
                setError("");
              }}
            >
              {t("cloudAccount.forgot")}
            </Button>
          )}
          {account?.status?.identifier && (
            <Button
              type="text"
              onClick={async () => {
                account.update(await cloudAccountApi.forget());
                form.setFieldValue("identifier", "");
              }}
            >
              {t("cloudAccount.forget")}
            </Button>
          )}
        </Space>
      </Card>
    </div>
  );
}
