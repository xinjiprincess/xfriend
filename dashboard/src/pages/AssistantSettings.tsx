import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Form,
  Input,
  Select,
  Space,
  Switch,
  Typography,
} from "antd";
import { voiceApi, type AssistantVoicePreferences } from "../api/modules/voice";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import PageShell from "../layouts/PageShell";
import StartupDiagnostics from "../components/StartupDiagnostics";
import VoiceLibrary from "../components/VoiceLibrary";
import { executionApi } from "../api/modules/execution";

export default function AssistantSettings() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [form] = Form.useForm<AssistantVoicePreferences>();
  const [targetForm] = Form.useForm<{
    expert_ids: string[];
    wake_word: string;
  }>();
  const [experts, setExperts] = useState<{ value: string; label: string }[]>(
    [],
  );
  const [microphones, setMicrophones] = useState<
    { value: string; label: string }[]
  >([]);
  const [microphone, setMicrophone] = useState(
    () => localStorage.getItem("xfriend_microphone") || "",
  );
  const [choices, setChoices] = useState<
    { value: string; label: string; capability: string }[]
  >([]);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let disposed = false;
    void Promise.all([
      voiceApi.getPreferences(),
      voiceApi.getProviders(),
      voiceApi.getPresets(),
      executionApi.assistant(),
      executionApi.experts(),
    ])
      .then(([preferences, providers, presets, target, executionExperts]) => {
        if (disposed) return;
        form.setFieldsValue(preferences);
        targetForm.setFieldsValue(target);
        setExperts(
          executionExperts.map((expert) => ({
            value: expert.id,
            label: `${expert.name} · ${expert.engine}`,
          })),
        );
        setChoices(
          [...presets, ...providers.filter((provider) => provider.enabled)]
            .filter(
              (provider, index, all) =>
                all.findIndex((item) => item.name === provider.name) === index,
            )
            .map((provider) => ({
              value:
                "id" in provider && typeof provider.id === "string"
                  ? provider.id
                  : provider.name,
              label: provider.name,
              capability: provider.capability,
            })),
        );
        setLoaded(true);
      })
      .catch(() => {
        if (!disposed) message.error(t("assistantSettings.failed"));
      });
    void navigator.mediaDevices
      ?.enumerateDevices()
      .then((devices) => {
        if (!disposed)
          setMicrophones(
            devices
              .filter((device) => device.kind === "audioinput")
              .map((device, index) => ({
                value: device.deviceId,
                label:
                  device.label ||
                  t("execution.microphoneNumber", { number: index + 1 }),
              })),
          );
      })
      .catch(() => {});
    return () => {
      disposed = true;
    };
  }, [form, targetForm, message, t]);
  const native = window as Window & {
    _wails?: { invoke?: (message: string) => void };
  };
  return (
    <PageShell
      title={t("assistantSettings.title")}
      subtitle={t("assistantSettings.subtitle")}
    >
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card title={t("assistantSettings.open")}>
          <Typography.Paragraph>
            {t("assistantSettings.wake")}
          </Typography.Paragraph>
          <Button
            type="primary"
            disabled={!native._wails?.invoke}
            onClick={() =>
              native._wails?.invoke?.(
                JSON.stringify({ type: "xfriend:show-assistant" }),
              )
            }
          >
            {t("assistantSettings.open")}
          </Button>
          <Button onClick={() => navigate("/execution")}>
            {t("execution.title")}
          </Button>
        </Card>
        <Card title={t("execution.assistantTarget")}>
          <Form
            form={targetForm}
            layout="vertical"
            disabled={!loaded}
            onFinish={async (values) => {
              try {
                await executionApi.setAssistant(
                  values.expert_ids,
                  values.wake_word,
                );
                localStorage.setItem(
                  "xfriend_assistant_preferences",
                  String(Date.now()),
                );
                message.success(t("assistantSettings.saved"));
              } catch {
                message.error(t("assistantSettings.failed"));
              }
            }}
          >
            <Form.Item name="expert_ids" label={t("execution.experts")}>
              <Select mode="multiple" maxCount={4} options={experts} />
            </Form.Item>
            <Form.Item
              name="wake_word"
              label={t("execution.wakeWord")}
              rules={[{ required: true, min: 2, max: 20 }]}
            >
              <Input maxLength={20} />
            </Form.Item>
            <Form.Item label={t("execution.microphone")}>
              <Select
                value={microphone}
                options={[
                  { value: "", label: t("execution.defaultMicrophone") },
                  ...microphones,
                ]}
                onChange={(value) => {
                  setMicrophone(value);
                  localStorage.setItem("xfriend_microphone", value);
                }}
              />
            </Form.Item>
            <Button type="primary" htmlType="submit">
              {t("assistantSettings.save")}
            </Button>
          </Form>
        </Card>
        <Card title={t("assistantSettings.voice")}>
          <Typography.Paragraph>
            {t("assistantSettings.voiceHint")}
          </Typography.Paragraph>
          <Form
            form={form}
            layout="vertical"
            disabled={!loaded || saving}
            onFinish={async (values) => {
              setSaving(true);
              try {
                await voiceApi.setPreferences(values);
                localStorage.setItem(
                  "xfriend_assistant_preferences",
                  String(Date.now()),
                );
                message.success(t("assistantSettings.saved"));
              } catch {
                message.error(t("assistantSettings.failed"));
              } finally {
                setSaving(false);
              }
            }}
          >
            {(["stt", "tts"] as const).map((capability) => (
              <Form.Item
                key={capability}
                name={capability}
                label={t(`assistantSettings.${capability}`)}
              >
                <Select
                  options={[
                    { value: "", label: t("assistantSettings.inherit") },
                    ...choices.filter(
                      (choice) =>
                        choice.capability === capability ||
                        choice.capability === "both",
                    ),
                  ]}
                />
              </Form.Item>
            ))}
            <Form.Item name="voice_id" label={t("assistantSettings.voiceId")}>
              <Input maxLength={200} />
            </Form.Item>
            <Form.Item
              name="auto_read"
              valuePropName="checked"
              label={t("assistantSettings.autoRead")}
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="auto_send"
              valuePropName="checked"
              label={t("assistantSettings.autoSend")}
            >
              <Switch />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={saving}>
              {t("assistantSettings.save")}
            </Button>
          </Form>
          <Button onClick={() => navigate("/admin/models?tab=voice")}>
            {t("assistantSettings.configure")}
          </Button>
        </Card>
        <VoiceLibrary
          onSelect={(voiceId, provider) => {
            form.setFieldsValue({ voice_id: voiceId, tts: provider });
            message.info(t("voiceLibrary.saveHint"));
          }}
          onDelete={(voiceId) => {
            if (form.getFieldValue("voice_id") === voiceId)
              form.setFieldValue("voice_id", "");
          }}
        />
        <Alert type="info" showIcon message={t("assistantSettings.privacy")} />
        <StartupDiagnostics />
      </Space>
    </PageShell>
  );
}
