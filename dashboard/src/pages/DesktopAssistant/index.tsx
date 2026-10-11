import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getAuthToken } from "../../api/request";
import ExecutionConsole from "../../components/ExecutionConsole";
import "./style.css";

const parentOrigin = "http://wails.localhost";
function notifyParent(data: Record<string, unknown>) {
  // Wails uses http://wails.localhost on Windows and wails:// on macOS.
  window.parent.postMessage(data, "*");
}

export default function DesktopAssistant() {
  const { t } = useTranslation();
  const [authenticated, setAuthenticated] = useState(false);
  useEffect(() => {
    const receive = (e: MessageEvent) => {
      if (
        e.source !== window.parent ||
        (e.origin !== parentOrigin && e.origin !== "wails://wails")
      )
        return;
      if (e.data?.type !== "xfriend:session") return;
      if (typeof e.data.token === "string" && e.data.token) {
        sessionStorage.setItem("auth_token", e.data.token);
        setAuthenticated(true);
      } else {
        setAuthenticated(false);
        sessionStorage.removeItem("auth_token");
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);
  return authenticated && getAuthToken() ? (
    <div className="xfriend-assistant">
      <ExecutionConsole compact />
    </div>
  ) : (
    <div className="xfriend-assistant assistant-login">
      <p>{t("assistant.login")}</p>
      <button onClick={() => notifyParent({ type: "xfriend:main" })}>
        {t("assistant.main")}
      </button>
    </div>
  );
}
