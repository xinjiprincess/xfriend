import { useTranslation } from "react-i18next";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Alert, Button, Spin } from "antd";
import {
  cloudAccountApi,
  usesCloudAccount,
  type CloudAccountStatus,
} from "../api/modules/cloudAccount";

const CloudAccountContext = createContext<{
  status?: CloudAccountStatus;
  update: (next: CloudAccountStatus) => void;
} | null>(null);

export function useCloudAccount() {
  return useContext(CloudAccountContext);
}

export function CloudAccountProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const enabled = usesCloudAccount();
  const [status, setStatus] = useState<CloudAccountStatus>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const revision = useRef(0);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const expected = revision.current;
      try {
        const value = await cloudAccountApi.status();
        if (active && expected === revision.current) {
          setStatus(value);
          setError(false);
        }
      } catch {
        if (active) setError(true);
      }
      if (active) timer = setTimeout(poll, 1500);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [enabled, retry]);
  useEffect(() => {
    if (!enabled || !status || status.state === "restoring") return;
    if (status.state === "authenticated") {
      if (["/login", "/register", "/setup"].includes(pathname))
        navigate("/chat", { replace: true });
    } else {
      const next = "/login";
      if (!["/login", "/register"].includes(pathname))
        navigate(next, { replace: true });
    }
  }, [enabled, status, pathname, navigate]);
  if (!enabled) return children;
  if (!status || status.state === "restoring")
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <img src="/xfriend-icon.png" width={64} alt="" />
        <p>Singu Xfriend</p>
        {error ? (
          <Alert
            type="error"
            message={t("cloudAccount.statusFailed")}
            action={
              <Button onClick={() => setRetry((v) => v + 1)}>
                {t("cloudAccount.retry")}
              </Button>
            }
          />
        ) : (
          <Spin />
        )}
      </div>
    );
  if (
    status.state !== "authenticated" &&
    !["/login", "/register"].includes(pathname)
  )
    return <Spin />;
  const identityKey =
    status.state === "authenticated"
      ? `${status.user?.user_id}:${status.space_id}`
      : "guest";
  return (
    <CloudAccountContext.Provider
      key={identityKey}
      value={{
        status,
        update: (next) => {
          revision.current++;
          setStatus(next);
        },
      }}
    >
      {children}
    </CloudAccountContext.Provider>
  );
}
