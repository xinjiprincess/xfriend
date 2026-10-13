import { Card, Input, Select, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { DualEnginePlan, ExecutionExpert } from "../api/modules/execution";

export default function DualEngineBuilder({
  experts,
  value,
  onChange,
  disabled,
}: {
  experts: ExecutionExpert[];
  value: DualEnginePlan;
  onChange: (plan: DualEnginePlan) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const options = experts.map((expert) => ({
    value: expert.id,
    label: expert.name,
  }));
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
          gap: 12,
        }}
      >
        {(["native", "dsh"] as const).map((engine) => (
          <Card size="small" key={engine} title={t(`engineHub.${engine}Group`)}>
            <Space direction="vertical" style={{ width: "100%" }}>
              <Typography.Text type="secondary">
                {t(`engineHub.${engine}Tools`)}
              </Typography.Text>
              <Select
                mode="multiple"
                maxCount={3}
                style={{ width: "100%" }}
                disabled={disabled}
                aria-label={t(`engineHub.${engine}Group`)}
                placeholder={t("engineHub.selectGroup")}
                value={value[`${engine}_experts`]}
                options={options}
                onChange={(ids) =>
                  onChange({ ...value, [`${engine}_experts`]: ids })
                }
              />
              {engine === "native" ? (
                <Link to="/experts">{t("engineHub.configurePlugins")}</Link>
              ) : (
                <>
                  <Input
                    disabled={disabled}
                    value={value.workspace}
                    placeholder={t("execution.workspace")}
                    aria-label={t("execution.workspace")}
                    onChange={(event) =>
                      onChange({ ...value, workspace: event.target.value })
                    }
                  />
                  <Link to="/engines/dsh">{t("engineHub.configureDsh")}</Link>
                </>
              )}
            </Space>
          </Card>
        ))}
      </div>
      <Card size="small" title={t("engineHub.summary")}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            {t("engineHub.summaryHint")}
          </Typography.Text>
          <Space wrap>
            <Select
              disabled={disabled}
              value={value.summary_engine}
              aria-label={t("engineHub.summaryEngine")}
              options={[
                { value: "native", label: "Native" },
                { value: "dsh", label: "DSH" },
              ]}
              onChange={(summary_engine) =>
                onChange({ ...value, summary_engine })
              }
            />
            <Select
              disabled={disabled}
              value={value.summary_expert || undefined}
              style={{ minWidth: 240 }}
              placeholder={t("engineHub.summaryExpert")}
              aria-label={t("engineHub.summaryExpert")}
              options={options}
              onChange={(summary_expert) =>
                onChange({ ...value, summary_expert })
              }
            />
          </Space>
        </Space>
      </Card>
      <Typography.Text type="secondary">
        {t("engineHub.orderHint")}
      </Typography.Text>
    </Space>
  );
}
