import { useEffect, useState } from "react";
import { Alert, App, Button, Select, Space, Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import PageShell from "../layouts/PageShell";
import { enginesApi, type DSHExpertView } from "../api/modules/engines";
import DSHWorkspace from "./Agent/ACP/DSHWorkspace";
import DSHOriginalWorkspace from "./DSHOriginalWorkspace";
import DSHSettings from "../components/DSHSettings";
import { useCurrentUser } from "../hooks/useCurrentUser";
import RootDirSelect from "./Experts/components/RootDirSelect";

export default function DSHExpert() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const user = useCurrentUser();
  const [state, setState] = useState<DSHExpertView>();
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    enginesApi
      .expert()
      .then(setState)
      .catch(() => message.error(t("engines.failed")));
  }, [message, t]);
  const save = async () => {
    if (!state) return;
    setSaving(true);
    try {
      setState(
        await enginesApi.setExpert(state.cwd, state.connectors, state.model),
      );
      message.success(t("execution.saved"));
    } catch {
      message.error(t("engines.failed"));
    } finally {
      setSaving(false);
    }
  };
  return (
    <PageShell
      title={t("dshExpert.name")}
      subtitle={t("dshExpert.description")}
    >
      {state?.available === false ? (
        <Alert type="info" showIcon message={t("dshExpert.missing")} />
      ) : (
        state && (
          <Tabs
            destroyOnHidden
            items={[
              {
                key: "chat",
                label: t("engineHub.inputPage"),
                children: <DSHWorkspace expert />,
              },
              {
                key: "workspace",
                label: t("engineHub.originalWorkspace"),
                children: <DSHOriginalWorkspace />,
              },
              {
                key: "settings",
                label: t("engineHub.settings"),
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Select
                      allowClear
                      style={{ width: "100%" }}
                      aria-label={t("dshExpert.models")}
                      placeholder={t("dshExpert.models")}
                      value={state.model || undefined}
                      options={state.models.map((item) => ({
                        value: item.id,
                        label: item.name,
                      }))}
                      onChange={(model) =>
                        setState({ ...state, model: model || "" })
                      }
                    />
                    <RootDirSelect
                      value={state.cwd}
                      onChange={(cwd) => setState({ ...state, cwd })}
                    />
                    <Button onClick={() => setState({ ...state, cwd: "" })}>
                      {t("dshExpert.defaultWorkspace")}
                    </Button>
                    <Select
                      mode="multiple"
                      style={{ width: "100%" }}
                      aria-label={t("dshExpert.connectors")}
                      placeholder={t("dshExpert.connectors")}
                      value={state.connectors}
                      options={state.choices.map((item) => ({
                        value: item.id,
                        label: item.name,
                        disabled: item.status !== "active",
                      }))}
                      onChange={(connectors) =>
                        setState({ ...state, connectors })
                      }
                    />
                    <Button loading={saving} onClick={() => void save()}>
                      {t("dshExpert.save")}
                    </Button>
                    <Button onClick={() => navigate("/connectors")}>
                      {t("dshExpert.manageConnectors")}
                    </Button>
                    <Button onClick={() => navigate("/models")}>
                      {t("dshExpert.models")}
                    </Button>
                    {user?.role === "admin" && <DSHSettings />}
                  </Space>
                ),
              },
            ]}
          />
        )
      )}
    </PageShell>
  );
}
