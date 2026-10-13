import { useState } from "react";
import { Alert, Button, Space } from "antd";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import PageShell from "../layouts/PageShell";
import EngineEntrypoints from "../components/EngineEntrypoints";
import ExecutionConsole from "../components/ExecutionConsole";
import ExpertTemplates from "../components/ExpertTemplates";
import DSHOriginalWorkspace from "./DSHOriginalWorkspace";
import DSHWorkspace from "./Agent/ACP/DSHWorkspace";
import DSHSettings from "../components/DSHSettings";
import EnginePackages from "../components/EnginePackages";

export default function Engines() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [revision, setRevision] = useState(0);
  const mode =
    pathname.endsWith("/original") ||
    pathname.endsWith("/settings") ||
    pathname.endsWith("/dsh")
      ? "dsh"
      : pathname.endsWith("/dual")
      ? "dual"
      : "hub";
  return (
    <PageShell
      title={t(`engineHub.${mode}Title`)}
      subtitle={t(`engineHub.${mode}Description`)}
      actions={
        mode !== "hub" ? (
          <Button onClick={() => navigate("/engines")}>
            {t("engineHub.back")}
          </Button>
        ) : undefined
      }
    >
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        {mode === "hub" && (
          <>
            <EngineEntrypoints />
            <Alert
              type="info"
              showIcon
              message={t("engineHub.architecture")}
              description={t("engineHub.architectureHint")}
            />
          </>
        )}
        {mode === "dsh" && (
          <>
            <Space wrap>
              <Button onClick={() => navigate("/engines/dsh")}>
                {t("engineHub.inputPage")}
              </Button>
              <Button onClick={() => navigate("/engines/dsh/original")}>
                {t("engineHub.originalWorkspace")}
              </Button>
              <Button onClick={() => navigate("/engines/settings")}>
                {t("engineHub.settings")}
              </Button>
            </Space>
            {pathname.endsWith("/original") ? (
              <DSHOriginalWorkspace />
            ) : pathname.endsWith("/settings") ? (
              <>
                <DSHSettings />
                <EnginePackages />
              </>
            ) : (
              <DSHWorkspace />
            )}
          </>
        )}
        {mode === "dual" && (
          <>
            <Alert
              type="info"
              showIcon
              message={t("engineHub.dualFlow")}
              description={t("engineHub.dualScope")}
            />
            <ExpertTemplates
              onCreated={() => setRevision((value) => value + 1)}
            />
            <ExecutionConsole key={revision} dual />
          </>
        )}
      </Space>
    </PageShell>
  );
}
