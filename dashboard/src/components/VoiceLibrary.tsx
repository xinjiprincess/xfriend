import { useEffect, useRef, useState } from "react";
import {
  App,
  Button,
  Card,
  Form,
  Input,
  List,
  Popconfirm,
  Select,
  Space,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import { voiceApi, type LibraryVoice } from "../api/modules/voice";

export default function VoiceLibrary({
  onSelect,
  onDelete,
}: {
  onSelect: (voiceId: string, provider: string) => void;
  onDelete: (voiceId: string) => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [voices, setVoices] = useState<LibraryVoice[]>([]);
  const [providers, setProviders] = useState<
    { value: string; label: string }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm<{
    name: string;
    provider: string;
    description: string;
    text: string;
  }>();
  const provider = Form.useWatch("provider", form);
  const name = Form.useWatch("name", form);
  const playback = useRef<{ audio: HTMLAudioElement; url: string }>();
  const generation = useRef(0);
  const stop = () => {
    generation.current += 1;
    if (playback.current) {
      playback.current.audio.pause();
      URL.revokeObjectURL(playback.current.url);
      playback.current = undefined;
    }
  };
  useEffect(() => {
    let disposed = false;
    void Promise.all([voiceApi.listLibrary(), voiceApi.getProviders()])
      .then(([items, services]) => {
        if (disposed) return;
        setVoices(items);
        setProviders(
          services
            .filter(
              (s) => s.enabled && s.kind === "mimo" && s.capability !== "stt",
            )
            .map((s) => ({ value: s.name, label: s.name })),
        );
      })
      .catch(() => {
        if (!disposed) message.error(t("voiceLibrary.failed"));
      });
    return () => {
      disposed = true;
      stop();
    };
  }, [message, t]);
  const update = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      setVoices(await voiceApi.listLibrary());
    } catch {
      message.error(t("voiceLibrary.failed"));
    } finally {
      setBusy(false);
    }
  };
  const preview = async (id: string) => {
    stop();
    const current = generation.current;
    try {
      const blob = await voiceApi.voiceSample(id);
      if (current !== generation.current) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      playback.current = { audio, url };
      audio.onended = () => {
        if (current === generation.current) stop();
      };
      await audio.play();
    } catch {
      stop();
      message.error(t("voiceLibrary.failed"));
    }
  };
  return (
    <Card title={t("voiceLibrary.title")}>
      <Typography.Paragraph>{t("voiceLibrary.hint")}</Typography.Paragraph>
      <Form
        form={form}
        layout="vertical"
        disabled={busy}
        onFinish={(values) => void update(() => voiceApi.designVoice(values))}
      >
        <Form.Item
          name="provider"
          label={t("voiceLibrary.provider")}
          rules={[{ required: true }]}
        >
          <Select options={providers} />
        </Form.Item>
        <Form.Item
          name="name"
          label={t("voiceLibrary.name")}
          rules={[{ required: true, whitespace: true }]}
        >
          <Input maxLength={100} />
        </Form.Item>
        <Form.Item
          name="description"
          label={t("voiceLibrary.description")}
          rules={[{ required: true, whitespace: true }]}
        >
          <Input.TextArea maxLength={1000} />
        </Form.Item>
        <Form.Item
          name="text"
          label={t("voiceLibrary.text")}
          rules={[{ required: true, whitespace: true }]}
        >
          <Input.TextArea maxLength={1000} />
        </Form.Item>
        <Space wrap>
          <Button htmlType="submit" type="primary" loading={busy}>
            {t("voiceLibrary.design")}
          </Button>
          <label>
            {t("voiceLibrary.import")}
            <input
              type="file"
              accept="audio/wav,audio/mpeg,.wav,.mp3"
              disabled={busy || !name?.trim()}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file)
                  void update(() => voiceApi.uploadVoice(name.trim(), file));
              }}
            />
          </label>
        </Space>
      </Form>
      <List
        dataSource={voices}
        renderItem={(voice) => (
          <List.Item
            actions={[
              <Button key="preview" onClick={() => void preview(voice.id)}>
                {t("voiceLibrary.preview")}
              </Button>,
              <Button
                key="select"
                disabled={!provider || busy}
                onClick={() => onSelect(`library:${voice.id}`, provider)}
              >
                {t("voiceLibrary.select")}
              </Button>,
              <Popconfirm
                key="delete"
                title={t("voiceLibrary.confirmDelete")}
                onConfirm={() =>
                  update(async () => {
                    stop();
                    await voiceApi.deleteVoice(voice.id);
                    onDelete(`library:${voice.id}`);
                  })
                }
              >
                <Button danger disabled={busy}>
                  {t("voiceLibrary.delete")}
                </Button>
              </Popconfirm>,
            ]}
          >
            <Typography.Text
              editable={{
                maxLength: 100,
                onChange: (value) => {
                  if (value.trim())
                    void update(() =>
                      voiceApi.renameVoice(voice.id, value.trim()),
                    );
                },
              }}
            >
              {voice.name}
            </Typography.Text>
          </List.Item>
        )}
      />
      <Button onClick={stop}>{t("voiceLibrary.stop")}</Button>
    </Card>
  );
}
