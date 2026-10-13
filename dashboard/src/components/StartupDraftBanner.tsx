import { useTranslation } from "react-i18next";
import { Button, Space } from "antd";
import { useStartupDraft } from "../context/StartupDraftContext";

export default function StartupDraftBanner({
  onInsert,
}: {
  onInsert: (text: string) => void;
}) {
  const { draft, setDraft } = useStartupDraft();
  const { t } = useTranslation();
  if (!draft.trim()) return null;
  return (
    <Space wrap style={{ padding: "8px 12px" }}>
      <span>{t("startup.draftReady")}</span>
      <Button
        size="small"
        onClick={() => {
          onInsert(draft);
          setDraft("");
        }}
      >
        {t("startup.insertDraft")}
      </Button>
      <Button size="small" type="text" onClick={() => setDraft("")}>
        {t("startup.discardDraft")}
      </Button>
    </Space>
  );
}
