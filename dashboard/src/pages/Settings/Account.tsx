import { useTranslation } from "react-i18next";
import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Select,
  Space,
  Typography,
} from "antd";
import { useNavigate } from "react-router-dom";
import { useCloudAccount } from "../../context/CloudAccountContext";
import { cloudAccountApi } from "../../api/modules/cloudAccount";
import { clearAuthToken } from "../../api/request";
import { authApi } from "../../api/modules/auth";

export default function AccountSettings() {
  const { t } = useTranslation();
  const account = useCloudAccount();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const logout = async () => {
    setBusy(true);
    setError("");
    try {
      if (account) account.update(await cloudAccountApi.logout());
      else await authApi.logout();
      clearAuthToken();
      navigate("/login", { replace: true });
    } catch {
      setError(t("cloudAccount.logoutFailed"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title={t("cloudAccount.settings")}>
      {error && <Alert type="error" message={error} />}
      {account?.status && (
        <>
          <Descriptions
            column={1}
            items={[
              {
                key: "name",
                label: t("cloudAccount.name"),
                children: account.status.user?.name,
              },
              {
                key: "account",
                label: t("cloudAccount.loginAccount"),
                children: account.status.user?.identifier,
              },
              {
                key: "keep",
                label: t("cloudAccount.keep"),
                children: account.status.keep_login
                  ? t("cloudAccount.on")
                  : t("cloudAccount.off"),
              },
            ]}
          />
          <Space wrap style={{ marginBottom: 24 }}>
            <Typography.Text>{t("cloudAccount.currentSpace")}</Typography.Text>
            <Select
              value={account.status.space_id}
              style={{ minWidth: 240 }}
              disabled={busy}
              options={account.status.spaces.map((s) => ({
                value: s.space_id,
                label: `${
                  s.kind === "enterprise"
                    ? t("cloudAccount.enterprise")
                    : t("cloudAccount.personal")
                } · ${s.name}`,
              }))}
              onChange={async (id) => {
                setBusy(true);
                setError("");
                try {
                  account.update(await cloudAccountApi.switchSpace(id));
                  window.location.replace("/chat");
                } catch (err) {
                  setError(
                    err instanceof Error
                      ? err.message
                      : t("cloudAccount.switchFailed"),
                  );
                } finally {
                  setBusy(false);
                }
              }}
            />
          </Space>
        </>
      )}
      <div>
        <Button danger loading={busy} onClick={() => void logout()}>
          {t("cloudAccount.logout")}
        </Button>
      </div>
    </Card>
  );
}
