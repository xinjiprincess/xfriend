import { useEffect, useState } from "react";
import { Alert, Button, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { enginesApi } from "../api/modules/engines";
import DSHWorkspace from "./Agent/ACP/DSHWorkspace";

export default function DSHOriginalWorkspace() {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string>();
  const [error, setError] = useState(false);
  const [legacy, setLegacy] = useState(false);

  useEffect(() => {
    let active = true;
    enginesApi
      .web()
      .then(({ url: address }) => {
        if (active) setUrl(address);
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div style={{ width: "100%" }}>
      <Button
        onClick={() => setLegacy((value) => !value)}
        style={{ marginBottom: 12 }}
      >
        {legacy
          ? t("engineHub.originalWorkspace")
          : t("engineHub.previousSessions")}
      </Button>
      {legacy ? (
        <DSHWorkspace />
      ) : error ? (
        <Alert
          type="error"
          showIcon
          message={t("engineHub.originalUnavailable")}
        />
      ) : url ? (
        <iframe
          title={t("engineHub.originalWorkspace")}
          src={url}
          referrerPolicy="no-referrer"
          style={{
            width: "100%",
            height: "calc(100vh - 260px)",
            minHeight: 550,
            border: 0,
          }}
        />
      ) : (
        <Spin tip={t("engineHub.originalLoading")} />
      )}
    </div>
  );
}
