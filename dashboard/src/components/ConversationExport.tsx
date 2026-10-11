import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Form,
  InputNumber,
  Modal,
  Select,
  Space,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  conversationDocumentsApi,
  type DocumentSelection,
  type ExportMessage,
} from "../api/modules/conversationDocuments";

function previewText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content
      .map((part) => (typeof part === "string" ? part : part?.text ?? ""))
      .join(" ");
  return "";
}

export default function ConversationExport({
  agent,
  thread,
  engine,
  open,
  onClose,
}: {
  agent: string;
  thread: string;
  engine?: "dsh" | "execution";
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [messages, setMessages] = useState<ExportMessage[]>([]);
  const [digest, setDigest] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [format, setFormat] = useState<DocumentSelection["format"]>("pdf");
  const [mode, setMode] = useState<DocumentSelection["mode"]>("all");
  const [indices, setIndices] = useState<number[]>([]);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    setLoading(true);
    setDigest("");
    setMessages([]);
    setIndices([]);
    setStart(0);
    setEnd(0);
    conversationDocumentsApi
      .preview(agent, thread, engine)
      .then((result) => {
        if (!disposed) {
          setMessages(result.messages);
          setDigest(result.digest);
          setEnd(Math.max(0, result.messages.length - 1));
        }
      })
      .catch(() => {
        if (!disposed) message.error(t("conversationExport.failed"));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [open, agent, thread, engine, message, t]);
  const options = messages.map((item, index) => ({
    value: index,
    label: `${index + 1}. ${item.agent_id ?? item.role}: ${previewText(
      item.content,
    ).slice(0, 80)}`,
  }));
  const exportDocument = async () => {
    setSaving(true);
    try {
      const blob = await conversationDocumentsApi.export(
        agent,
        thread,
        { format, mode, indices, start, end, digest },
        engine,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `Xfriend-conversation.${
        format === "png" ? "zip" : format
      }`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      onClose();
    } catch {
      message.error(t("conversationExport.failed"));
    } finally {
      setSaving(false);
    }
  };
  const invalid =
    !digest ||
    !messages.length ||
    (mode === "selected" && !indices.length) ||
    (mode === "range" && start > end) ||
    (mode === "turn" && !["user", "human"].includes(messages[start]?.role));
  return (
    <Modal
      open={open}
      title={t("conversationExport.title")}
      onCancel={onClose}
      footer={null}
      destroyOnClose
    >
      <Form layout="vertical">
        <Alert
          type="info"
          message={t("conversationExport.hint")}
          style={{ marginBottom: 16 }}
        />
        <Form.Item label={t("conversationExport.format")}>
          <Select
            value={format}
            onChange={setFormat}
            options={["pdf", "html", "docx", "png"].map((value) => ({
              value,
              label:
                value === "png"
                  ? t("conversationExport.images")
                  : value.toUpperCase(),
            }))}
          />
        </Form.Item>
        <Form.Item label={t("conversationExport.scope")}>
          <Select
            value={mode}
            onChange={setMode}
            options={["all", "turn", "selected", "range"].map((value) => ({
              value,
              label: t(`conversationExport.${value}`),
            }))}
          />
        </Form.Item>
        {mode === "turn" && (
          <Form.Item label={t("conversationExport.turn")}>
            <Select
              loading={loading}
              value={start}
              onChange={setStart}
              options={options.filter((option) =>
                ["user", "human"].includes(messages[option.value].role),
              )}
            />
          </Form.Item>
        )}
        {mode === "selected" && (
          <Form.Item label={t("conversationExport.selected")}>
            <Select
              loading={loading}
              mode="multiple"
              value={indices}
              onChange={setIndices}
              options={options}
              optionFilterProp="label"
            />
          </Form.Item>
        )}
        {mode === "range" && (
          <Form.Item label={t("conversationExport.range")}>
            <Space>
              <InputNumber
                min={1}
                max={messages.length}
                value={start + 1}
                onChange={(value) => setStart((value ?? 1) - 1)}
              />
              <span>—</span>
              <InputNumber
                min={1}
                max={messages.length}
                value={end + 1}
                onChange={(value) => setEnd((value ?? 1) - 1)}
              />
            </Space>
          </Form.Item>
        )}
        <Button
          type="primary"
          loading={saving || loading}
          disabled={invalid}
          onClick={() => void exportDocument()}
        >
          {t("conversationExport.download")}
        </Button>
      </Form>
    </Modal>
  );
}
