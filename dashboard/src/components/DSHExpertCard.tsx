import { useEffect, useState } from "react";
import { Button, Card, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { enginesApi } from "../api/modules/engines";

export default function DSHExpertCard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [available, setAvailable] = useState<boolean>();
  useEffect(() => {
    let active = true;
    enginesApi
      .expert()
      .then((value) => {
        if (active) setAvailable(value.available);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  return (
    <Card
      title={t("dshExpert.name")}
      style={{ marginBottom: 16 }}
      extra={<Tag>DSH</Tag>}
    >
      <Typography.Paragraph>
        {t(available === false ? "dshExpert.missing" : "dshExpert.description")}
      </Typography.Paragraph>
      <Button type="primary" onClick={() => navigate("/experts/dsh")}>
        {t("dshExpert.open")}
      </Button>
    </Card>
  );
}
