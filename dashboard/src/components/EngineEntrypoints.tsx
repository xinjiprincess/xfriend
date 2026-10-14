import { Card, Space, Typography, Button } from "antd";
import { Cpu, Network, MessageSquare } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { useCurrentUser } from "../hooks/useCurrentUser";

export default function EngineEntrypoints({
  compact = false,
}: {
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useCurrentUser();
  const items = [
    {
      key: "native",
      path: "/execution",
      icon: <MessageSquare size={20} />,
      admin: false,
    },
    { key: "dsh", path: "/experts/dsh", icon: <Cpu size={20} />, admin: false },
    {
      key: "dual",
      path: "/engines/dual",
      icon: <Network size={20} />,
      admin: true,
    },
  ];
  if (compact)
    return (
      <Space wrap>
        {items.map((item) => (
          <Button
            key={item.key}
            icon={item.icon}
            disabled={item.admin && user?.role !== "admin"}
            onClick={() => navigate(item.path)}
          >
            {t(`engineHub.${item.key}Title`)}
          </Button>
        ))}
      </Space>
    );
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
        gap: 16,
      }}
    >
      {items.map((item) => (
        <Card
          key={item.key}
          title={
            <Space>
              {item.icon}
              {t(`engineHub.${item.key}Title`)}
            </Space>
          }
        >
          <Typography.Paragraph>
            {t(`engineHub.${item.key}Description`)}
          </Typography.Paragraph>
          <Button
            type="primary"
            disabled={item.admin && user?.role !== "admin"}
            onClick={() => navigate(item.path)}
          >
            {t("engineHub.open")}
          </Button>
          {item.admin && user?.role !== "admin" && (
            <Typography.Paragraph type="secondary">
              {t("engineHub.adminOnly")}
            </Typography.Paragraph>
          )}
        </Card>
      ))}
    </div>
  );
}
