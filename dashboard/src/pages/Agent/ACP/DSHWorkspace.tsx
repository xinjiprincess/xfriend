import { useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Input,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import ConversationExport from "../../../components/ConversationExport";
import {
  enginesApi,
  type EngineSession,
  type EngineSessionSummary,
} from "../../../api/modules/engines";

export default function DSHWorkspace() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [sessions, setSessions] = useState<EngineSessionSummary[]>([]);
  const [session, setSession] = useState<EngineSession | null>(null);
  const [selected, setSelected] = useState<string>();
  const [cwd, setCwd] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const active = useRef(selected);
  active.current = selected;

  useEffect(() => {
    let disposed = false;
    enginesApi
      .list()
      .then((rows) => {
        if (!disposed) setSessions(rows);
      })
      .catch(() => {
        if (!disposed) message.error(t("engines.failed"));
      });
    return () => {
      disposed = true;
    };
  }, [message, t]);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    if (!selected) return;
    setSession(null);
    const poll = async () => {
      try {
        const next = await enginesApi.get(selected);
        if (!disposed) setSession(next);
      } catch {
        if (!disposed) message.error(t("engines.failed"));
        return;
      }
      if (!disposed) timer = setTimeout(poll, 1000);
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [selected, message, t]);

  const run = async (action: () => Promise<EngineSession>) => {
    const expected = active.current;
    setBusy(true);
    try {
      const next = await action();
      if (active.current === expected) {
        setSelected(next.id);
        setSession(next);
      }
      setSessions(await enginesApi.list());
      return true;
    } catch {
      message.error(t("engines.failed"));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const running =
    session?.state === "running" || session?.state === "permission";

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message={t("engines.title")}
        description={t("engines.hint")}
      />
      <Space wrap>
        <Input
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          placeholder={t("engines.cwd")}
          style={{ width: 360 }}
        />
        <Button
          disabled={!cwd.trim() || busy}
          onClick={() => void run(() => enginesApi.create(cwd))}
        >
          {t("engines.create")}
        </Button>
        <Select
          value={selected}
          onChange={setSelected}
          placeholder={t("engines.choose")}
          style={{ minWidth: 260 }}
          options={sessions.map((item) => ({
            value: item.id,
            label: `${item.cwd} · ${item.id.slice(0, 8)}`,
          }))}
        />
      </Space>
      <Typography.Text type="secondary">
        {t("engineHub.dshTools")}
      </Typography.Text>
      {session && (
        <>
          <Button onClick={() => setExportOpen(true)}>
            {t("conversationExport.title")}
          </Button>
          <ConversationExport
            agent=""
            engine="dsh"
            thread={session.id}
            open={exportOpen}
            onClose={() => setExportOpen(false)}
          />
          <Space>
            <Tag>DSH</Tag>
            <Tag>{session.engine_version}</Tag>
            {session.extensions?.map((name) => <Tag key={name}>{name}</Tag>)}
            <Typography.Text>
              {t(`engines.states.${session.state}`, {
                defaultValue: session.state,
              })}
            </Typography.Text>
          </Space>
          {session.error && (
            <Alert
              type="error"
              message={t("engines.executionFailed")}
              description={
                session.error === "ENGINE_VERSION_CHANGED"
                  ? t("engines.versionChanged")
                  : session.error
              }
            />
          )}
          <div
            style={{
              maxHeight: "50vh",
              overflowY: "auto",
              display: "grid",
              gap: 12,
            }}
          >
            {session.messages.map((item, index) => (
              <Card
                size="small"
                key={index}
                title={
                  item.role === "user" ? t("engines.you") : t("engines.expert")
                }
              >
                <div
                  style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                >
                  {item.text}
                </div>
                {!!item.tools?.length && (
                  <Typography.Text type="secondary">
                    {t("engines.tools", { count: item.tools.length })}
                  </Typography.Text>
                )}
              </Card>
            ))}
          </div>
          {session.permission && (
            <Card title={t("engines.permission")}>
              <p>{session.permission.title}</p>
              <Space wrap>
                {session.permission.options.map((option) => {
                  const id = option.option_id ?? option.optionId;
                  return (
                    <Button
                      key={id}
                      disabled={busy || !id}
                      onClick={() =>
                        id &&
                        void run(() => enginesApi.permission(session.id, id))
                      }
                    >
                      {option.name}
                    </Button>
                  );
                })}
              </Space>
            </Card>
          )}
          <Input.TextArea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            placeholder={t("engines.prompt")}
            disabled={busy || running}
          />
          <Space>
            <Button
              type="primary"
              disabled={busy || running || !text.trim()}
              onClick={async () => {
                if (await run(() => enginesApi.prompt(session.id, text)))
                  setText("");
              }}
            >
              {t("engines.send")}
            </Button>
            <Button
              danger
              disabled={busy || !running}
              onClick={() => void run(() => enginesApi.cancel(session.id))}
            >
              {t("engines.cancel")}
            </Button>
          </Space>
        </>
      )}
    </Space>
  );
}
