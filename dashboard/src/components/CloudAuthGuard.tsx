import { useTranslation } from "react-i18next";
import { useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Spin } from "antd";
import { authApi, type OctopUser } from "../api/modules/auth";
import { useCloudAccount } from "../context/CloudAccountContext";
import { useStartupDraft } from "../context/StartupDraftContext";
import { CurrentUserProvider } from "../hooks/useCurrentUser";

export default function CloudAuthGuard({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const account = useCloudAccount();
  const { ready, startupError } = useStartupDraft();
  const [user, setUser] = useState<OctopUser | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const identity = account?.status?.user?.user_id;
  const space = account?.status?.space_id;
  useEffect(() => {
    setUser(null);
    setError("");
    if (!ready || account?.status?.state !== "authenticated") return;
    let active = true;
    void authApi
      .me()
      .then((me) => {
        if (active) setUser(me);
      })
      .catch(() => {
        if (active) setError(t("cloudAccount.workspaceFailed"));
      });
    return () => {
      active = false;
    };
  }, [ready, identity, space, account?.status?.state, retry]);
  if (!user || account?.status?.state !== "authenticated")
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <h2>Singu Xfriend</h2>
        {error || startupError ? (
          <Alert
            type="error"
            message={error || startupError}
            action={
              <Button onClick={() => setRetry((v) => v + 1)}>
                {t("cloudAccount.retry")}
              </Button>
            }
          />
        ) : (
          <>
            <Spin />
            <p>{t("cloudAccount.opening")}</p>
          </>
        )}
      </div>
    );
  return (
    <CurrentUserProvider user={user} setUser={setUser}>
      {children}
    </CurrentUserProvider>
  );
}
