import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Card, Input, Space, Spin, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { licensingApi, type ProductLicense } from "../api/modules/licensing";

export default function LicenseGate({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const [license, setLicense] = useState<ProductLicense>();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [code, setCode] = useState("");
  const localTrial = license?.mode === "local_trial";
  const remainingDays = license?.expires_at
    ? Math.max(
        0,
        Math.ceil((license.expires_at * 1000 - Date.now()) / 86_400_000),
      )
    : 0;
  const stateLabel = localTrial
    ? t(`licensing.localStates.${license.state}`)
    : t(`licensing.states.${license?.state || "unactivated"}`);

  const sync = useCallback(async (manual = false, activationCode?: string) => {
    setBusy(true);
    try {
      const result = manual
        ? await licensingApi.activate(activationCode)
        : await licensingApi.sync();
      setLicense(result);
      setFailed(false);
      if (activationCode && result.valid && !result.sync_error) setCode("");
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    let live = true;
    void licensingApi.status().then(
      (result) => {
        if (!live) return;
        setLicense(result);
        if (result.required) void sync();
      },
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [sync]);

  useEffect(() => {
    if (!license?.required) return;
    const timer = window.setInterval(() => void sync(), 60_000);
    const refresh = () => void sync();
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [license?.required, sync]);

  if (!license && !failed) {
    return <Spin fullscreen tip={t("licensing.checking")} />;
  }
  if (license?.valid && location.pathname !== "/activation") {
    return (
      <>
        {children}
        {license.required && location.pathname !== "/desktop-assistant" && (
          <Button
            size="small"
            style={{ position: "fixed", right: 18, bottom: 12, zIndex: 1000 }}
            onClick={() => navigate("/activation")}
          >
            {license.edition === "trial"
              ? t("licensing.remaining", { count: remainingDays })
              : stateLabel}
          </Button>
        )}
      </>
    );
  }

  const expires = license?.expires_at
    ? new Intl.DateTimeFormat(i18n.language, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: license.timezone,
      }).format(new Date(license.expires_at * 1000))
    : null;
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
        background: "var(--color-bg-layout)",
      }}
    >
      <Card style={{ width: "100%", maxWidth: 560 }}>
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Space>
            <img src="/xfriend-icon.png" width={52} height={52} alt="" />
            <Typography.Title level={3} style={{ margin: 0 }}>
              Singu Xfriend
            </Typography.Title>
          </Space>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {localTrial
              ? t("licensing.localTitle", { days: license.trial_days })
              : t("licensing.title")}
          </Typography.Title>
          <Typography.Paragraph>
            {localTrial
              ? t("licensing.localDescription", { days: license.trial_days })
              : t("licensing.description")}
          </Typography.Paragraph>
          <Tag color={license?.valid ? "green" : "orange"}>
            {busy ? t("licensing.checking") : stateLabel}
          </Tag>
          {expires && (
            <Typography.Text>
              {t("licensing.expires", { date: expires })}
            </Typography.Text>
          )}
          {license?.device_id && (
            <div>
              <Typography.Text type="secondary">
                {t("licensing.device")}
              </Typography.Text>
              <Typography.Paragraph copyable style={{ wordBreak: "break-all" }}>
                {license.device_id}
              </Typography.Paragraph>
            </div>
          )}
          {(failed || license?.sync_error) && (
            <Alert
              type="warning"
              showIcon
              message={
                localTrial
                  ? t("licensing.localReadError")
                  : t(
                      `licensing.errors.${
                        license?.sync_error || "unavailable"
                      }`,
                    )
              }
            />
          )}
          <Button
            type="primary"
            loading={busy}
            onClick={() => void sync(true)}
            block
          >
            {t(localTrial ? "licensing.recheck" : "licensing.sync")}
          </Button>
          {!localTrial && (
            <>
              <Input.Password
                value={code}
                onChange={(event) => setCode(event.target.value)}
                placeholder={t("licensing.code")}
                autoComplete="off"
                maxLength={128}
              />
              <Button
                disabled={!code.trim() || busy}
                onClick={() => void sync(true, code)}
                block
              >
                {t("licensing.redeem")}
              </Button>
            </>
          )}
          {license?.valid && (
            <Button onClick={() => navigate("/")} block>
              {t("licensing.enter")}
            </Button>
          )}
        </Space>
      </Card>
    </div>
  );
}
