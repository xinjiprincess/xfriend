import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  MessageSquare,
  Users,
  Settings,
  Workflow,
  RefreshCw,
} from "lucide-react";
import {
  readDesktopStartup,
  type DesktopStartupStatus,
} from "../api/modules/desktopStartup";
import { StartupDraftContext } from "../context/StartupDraftContext";
import { usesDesktopStartup } from "../utils/desktopStartup";
import { usesCloudAccount } from "../api/modules/cloudAccount";
import "./StartupGate.css";

const destinations = [
  { path: "/chat", key: "chat", Icon: MessageSquare },
  { path: "/experts", key: "experts", Icon: Users },
  { path: "/engines", key: "engines", Icon: Workflow },
  { path: "/assistant-settings", key: "assistant", Icon: Settings },
] as const;

export default function StartupGate({ children }: { children: ReactNode }) {
  const [desktop] = useState(usesDesktopStartup);
  const [status, setStatus] = useState<DesktopStartupStatus>();
  const [unreachable, setUnreachable] = useState(false);
  const [draft, setDraft] = useState("");
  const [destination, setDestination] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const ready = !desktop || status?.state === "ready";
  const failed = unreachable || status?.state === "failed";
  const startupError = failed
    ? status?.message || t("startup.connectionError")
    : null;
  const value = useMemo(
    () => ({
      ready,
      startupError,
      draft,
      setDraft,
      destination,
      setDestination,
    }),
    [ready, startupError, draft, destination],
  );

  useEffect(() => {
    if (!desktop || ready) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let current: AbortController | undefined;
    const probe = async () => {
      current = new AbortController();
      const timeout = setTimeout(() => current?.abort(), 2500);
      let complete = false;
      try {
        const result = await readDesktopStartup(current.signal);
        if (!active) return;
        setStatus(result);
        setUnreachable(false);
        complete = result.state !== "starting";
      } catch {
        if (active) setUnreachable(true);
      } finally {
        clearTimeout(timeout);
        if (active && !complete) timer = setTimeout(probe, 750);
      }
    };
    void probe();
    return () => {
      active = false;
      clearTimeout(timer);
      current?.abort();
    };
  }, [desktop, ready, retry]);

  const publicAuthPage =
    location.pathname === "/login" || location.pathname === "/setup";
  return (
    <StartupDraftContext.Provider value={value}>
      {ready ||
      publicAuthPage ||
      usesCloudAccount() ||
      location.pathname === "/register" ? (
        children
      ) : (
        <div className="xf-startup">
          <aside className="xf-startup-sidebar">
            <div className="xf-startup-brand">
              <img src="/xfriend-icon.png" alt="" />
              <strong>Singu Xfriend</strong>
            </div>
            <nav aria-label={t("startup.navigation")}>
              {destinations.map(({ path, key, Icon }) => (
                <button
                  key={path}
                  aria-current={
                    location.pathname.startsWith(path) ? "page" : undefined
                  }
                  onClick={() => {
                    setDestination(path);
                    navigate(path);
                  }}
                >
                  <Icon size={19} />
                  {t(`startup.nav.${key}`)}
                </button>
              ))}
            </nav>
            <p>{t("startup.navigationHint")}</p>
          </aside>
          <main className="xf-startup-main">
            <div className="xf-startup-status" role="status" aria-live="polite">
              <span
                className={failed ? "xf-startup-dot failed" : "xf-startup-dot"}
              />
              <span>{t(failed ? "startup.failed" : "startup.preparing")}</span>
              {!!status?.elapsed_ms && (
                <span className="xf-startup-time">
                  {t("startup.elapsed", {
                    seconds: Math.floor(status.elapsed_ms / 1000),
                  })}
                </span>
              )}
            </div>
            <section className="xf-startup-compose">
              <img
                className="xf-startup-mascot"
                src="/xfriend-icon.png"
                alt=""
              />
              <h1>{t("startup.title")}</h1>
              <p>{t("startup.description")}</p>
              {failed && (
                <div className="xf-startup-error" role="alert">
                  <p>
                    {typeof status?.message === "string" && status.message
                      ? status.message
                      : t("startup.connectionError")}
                  </p>
                  <button onClick={() => setRetry((n) => n + 1)}>
                    <RefreshCw size={16} /> {t("startup.retry")}
                  </button>
                </div>
              )}
              <label htmlFor="xf-startup-draft">
                {t("startup.draftLabel")}
              </label>
              <textarea
                id="xf-startup-draft"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={t("startup.placeholder")}
                maxLength={100000}
                rows={5}
              />
              <small>{t("startup.draftHint")}</small>
            </section>
          </main>
        </div>
      )}
    </StartupDraftContext.Provider>
  );
}
