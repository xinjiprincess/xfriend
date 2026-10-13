import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Collapse,
  Steps,
  Input,
  List,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  executionApi,
  type ExecutionExpert,
  type ExecutionTask,
  type ExecutionTaskSummary,
  type DualEnginePlan,
} from "../api/modules/execution";
import { voiceApi, type AssistantVoicePreferences } from "../api/modules/voice";
import { useVoiceOutput } from "../hooks/useVoiceOutput";
import { useAssistantVoice } from "../pages/DesktopAssistant/useAssistantVoice";
import ConversationExport from "./ConversationExport";
import DualEngineBuilder from "./DualEngineBuilder";

const activeStates = new Set(["queued", "running", "permission"]);
export default function ExecutionConsole({
  compact = false,
  dual = false,
}: {
  compact?: boolean;
  dual?: boolean;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [experts, setExperts] = useState<ExecutionExpert[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [plan, setPlan] = useState<DualEnginePlan>({
    native_experts: [],
    dsh_experts: [],
    workspace: "",
    summary_expert: "",
    summary_engine: "native",
  });
  const [tasks, setTasks] = useState<ExecutionTaskSummary[]>([]);
  const [task, setTask] = useState<ExecutionTask>();
  const [taskId, setTaskId] = useState<string>();
  const [text, setText] = useState("");
  const [permissionText, setPermissionText] = useState("");
  const [engine, setEngine] = useState("native");
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [preferences, setPreferences] = useState<AssistantVoicePreferences>();
  const { speak, stop, speakingId } = useVoiceOutput({
    tts: preferences?.tts,
    voiceId: preferences?.voice_id,
  });
  const read = useRef<string>();
  const viewingHistory = useRef(false);
  const active = Boolean(task && activeStates.has(task.state));
  const ready = dual
    ? Boolean(
        plan.native_experts.length &&
          plan.dsh_experts.length &&
          plan.workspace.trim() &&
          plan.summary_expert,
      )
    : selected.length > 0;
  const fail = useCallback(() => {
    message.error(t("execution.failed"));
  }, [message, t]);
  useEffect(() => {
    let disposed = false;
    void Promise.all([
      executionApi.experts(),
      executionApi.list(),
      executionApi.assistant(),
      voiceApi.getPreferences(),
    ])
      .then(([items, history, target, prefs]) => {
        if (disposed) return;
        setExperts(items);
        setTasks(
          history.filter((item) => !dual || item.mode === "dual").reverse(),
        );
        setPreferences(prefs);
        const requested = new URLSearchParams(window.location.search).get(
          "expert",
        );
        setSelected(
          requested && items.some((expert) => expert.id === requested)
            ? [requested]
            : target.expert_ids.filter((id) =>
                items.some((expert) => expert.id === id),
              ),
        );
        if (dual) {
          const members = new URLSearchParams(window.location.search)
            .getAll("member")
            .filter((id) => items.some((item) => item.id === id));
          const native = (
            members.length
              ? members
              : items
                  .filter((item) => item.engine === "native")
                  .map((item) => item.id)
          ).slice(0, 1);
          const dsh = (
            members.length > 1
              ? members.slice(1)
              : items
                  .filter((item) => item.engine === "dsh")
                  .map((item) => item.id)
          ).slice(0, 3);
          setPlan({
            native_experts: native,
            dsh_experts: dsh,
            summary_expert: native[0] || "",
            summary_engine: "native",
            workspace:
              items.find((item) => dsh.includes(item.id))?.workspace || "",
          });
        }
        if (compact)
          window.parent.postMessage(
            { type: "xfriend:preferences", wakeWord: target.wake_word },
            "*",
          );
      })
      .catch(fail);
    return () => {
      disposed = true;
    };
  }, [fail, compact, dual]);
  useEffect(() => {
    if (!taskId) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await executionApi.get(taskId);
        if (disposed) return;
        if (viewingHistory.current) {
          read.current = `${value.id}:${value.messages.length}`;
          viewingHistory.current = false;
        }
        setTask(value);
        setSelected(value.steps.map((step) => step.expert_id));
        if (value.mode === "dual") {
          const summary = value.steps.find((step) => step.phase === "summary");
          setPlan({
            native_experts: value.steps
              .filter(
                (step) => step.engine === "native" && step.phase !== "summary",
              )
              .map((step) => step.expert_id),
            dsh_experts: value.steps
              .filter(
                (step) => step.engine === "dsh" && step.phase !== "summary",
              )
              .map((step) => step.expert_id),
            workspace:
              value.steps.find((step) => step.engine === "dsh")?.workspace ||
              "",
            summary_expert: summary?.expert_id || "",
            summary_engine: summary?.engine === "dsh" ? "dsh" : "native",
          });
        }
        if (activeStates.has(value.state)) timer = setTimeout(poll, 500);
        else
          setTasks((items) => [
            value,
            ...items.filter((item) => item.id !== value.id),
          ]);
      } catch {
        if (!disposed) fail();
      }
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [taskId, task?.state, fail]);
  useEffect(() => {
    const expert =
      selected.length === 1
        ? experts.find((item) => item.id === selected[0])
        : undefined;
    if (expert) {
      setEngine(expert.engine);
      setWorkspace(expert.workspace);
    }
  }, [selected, experts]);
  const send = useCallback(
    async (value: string) => {
      if (
        !value.trim() ||
        !ready ||
        sending.current ||
        active ||
        (taskId && !task)
      )
        return;
      sending.current = true;
      setBusy(true);
      stop();
      try {
        const result =
          task?.state === "succeeded"
            ? await executionApi.input(task.id, value)
            : dual
            ? await executionApi.createDual(plan, value)
            : await executionApi.create(selected, value);
        setTask(result);
        setTaskId(result.id);
        setText("");
      } catch {
        fail();
      } finally {
        sending.current = false;
        setBusy(false);
      }
    },
    [selected, ready, dual, plan, active, task, taskId, stop, fail],
  );
  const recognized = useCallback(
    async (value: string) => {
      setText(value);
      if (preferences?.auto_send) await send(value);
    },
    [preferences?.auto_send, send],
  );
  const voice = useAssistantVoice(recognized, fail);
  useEffect(() => {
    if (!compact) return;
    const notify = (data: unknown) => window.parent.postMessage(data, "*");
    notify({ type: "xfriend:voice-mode", service: voice.service });
    notify({
      type: "xfriend:busy",
      busy: active || busy || voice.busy || Boolean(speakingId),
    });
    const receive = (event: MessageEvent) => {
      if (
        event.source !== window.parent ||
        !["http://wails.localhost", "wails://wails"].includes(event.origin)
      )
        return;
      if (event.data?.type === "xfriend:stop-recording") {
        voice.stop();
        stop();
      }
      if (
        event.data?.type === "xfriend:wake" &&
        !active &&
        !busy &&
        !speakingId
      )
        void voice.record();
      if (
        event.data?.type === "xfriend:voice" &&
        typeof event.data.text === "string"
      )
        void recognized(event.data.text);
      if (
        event.data?.type === "xfriend:wake-word" &&
        typeof event.data.word === "string"
      )
        void executionApi.setAssistant(selected, event.data.word).catch(fail);
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [
    compact,
    voice,
    active,
    busy,
    speakingId,
    recognized,
    stop,
    selected,
    fail,
  ]);
  useEffect(() => {
    if (!preferences?.auto_read || task?.state !== "succeeded") return;
    const key = `${task.id}:${task.messages.length}`;
    const last = task.messages.at(-1);
    if (last?.role === "assistant" && read.current !== key) {
      read.current = key;
      speak(key, last.content);
    }
  }, [preferences?.auto_read, task, speak]);
  useEffect(() => () => stop(), [stop]);
  useEffect(() => {
    let disposed = false;
    const refresh = (event: StorageEvent) => {
      if (event.key !== "xfriend_assistant_preferences") return;
      void Promise.all([executionApi.assistant(), voiceApi.getPreferences()])
        .then(([target, prefs]) => {
          if (disposed) return;
          stop();
          setPreferences(prefs);
          if (!taskId) setSelected(target.expert_ids);
          if (compact)
            window.parent.postMessage(
              { type: "xfriend:preferences", wakeWord: target.wake_word },
              "*",
            );
        })
        .catch(fail);
    };
    window.addEventListener("storage", refresh);
    return () => {
      disposed = true;
      window.removeEventListener("storage", refresh);
    };
  }, [compact, taskId, stop, fail]);
  const act = async (action: () => Promise<ExecutionTask>) => {
    setBusy(true);
    try {
      const result = await action();
      setTask(result);
      setTaskId(result.id);
    } catch {
      fail();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Space
      direction="vertical"
      style={{ width: "100%", padding: compact ? 8 : 0 }}
    >
      {!compact && (
        <Alert type="info" showIcon message={t("execution.scope")} />
      )}
      {dual ? (
        <DualEngineBuilder
          experts={experts}
          value={plan}
          onChange={setPlan}
          disabled={active || busy || Boolean(task)}
        />
      ) : (
        <Select
          mode="multiple"
          value={selected}
          maxCount={4}
          style={{ width: "100%" }}
          disabled={active || busy || Boolean(task)}
          placeholder={t("execution.select")}
          onChange={setSelected}
          options={experts.map((expert) => ({
            value: expert.id,
            label: `${expert.name} · ${expert.engine}`,
          }))}
        />
      )}
      <Space wrap>
        <Button
          disabled={active || busy}
          onClick={() => {
            stop();
            setTask(undefined);
            setTaskId(undefined);
          }}
        >
          {t("execution.new")}
        </Button>
        {!dual && (
          <Button
            disabled={!selected.length || busy}
            onClick={() =>
              void executionApi
                .setAssistant(selected)
                .then(() => message.success(t("execution.saved")))
                .catch(fail)
            }
          >
            {t("execution.default")}
          </Button>
        )}
      </Space>
      {!compact && !dual && (
        <Card size="small" title={t("execution.binding")}>
          <Space wrap>
            <Select
              value={engine}
              onChange={setEngine}
              options={[
                { value: "native", label: "Native" },
                { value: "dsh", label: "DSH" },
              ]}
            />
            {engine === "dsh" && (
              <Input
                value={workspace}
                onChange={(event) => setWorkspace(event.target.value)}
                placeholder={t("execution.workspace")}
                style={{ width: 300 }}
              />
            )}
            <Button
              disabled={selected.length !== 1 || busy || active}
              onClick={() => {
                setBusy(true);
                void executionApi
                  .bind(selected[0], engine, workspace)
                  .then(() => executionApi.experts())
                  .then(setExperts)
                  .catch(fail)
                  .finally(() => setBusy(false));
              }}
            >
              {t("execution.bind")}
            </Button>
          </Space>
        </Card>
      )}
      {!compact && (
        <Select
          style={{ width: "100%" }}
          value={taskId}
          placeholder={t("execution.history")}
          disabled={active || busy}
          onChange={(id) => {
            viewingHistory.current = true;
            stop();
            setTask(undefined);
            setTaskId(id);
          }}
          options={tasks.map((item) => ({
            value: item.id,
            label: `${item.steps.map((step) => step.name).join(" → ")} · ${t(
              `execution.states.${item.state}`,
            )} · ${item.id.slice(0, 8)}`,
          }))}
        />
      )}
      {task && (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Tag>{t(`execution.states.${task.state}`)}</Tag>
          <Steps
            size="small"
            direction={compact ? "vertical" : "horizontal"}
            current={task.state === "succeeded" ? task.steps.length : task.step}
            status={task.state === "failed" ? "error" : "process"}
            items={task.steps.map((step) => ({
              title:
                step.phase === "summary"
                  ? `${t("engineHub.summary")} · ${step.name}`
                  : step.name,
              description: `${step.engine} ${step.engine_version || ""}`,
            }))}
          />
          <Typography.Text type="secondary">
            {t("execution.elapsed", {
              seconds: Math.max(0, task.updated_at - task.created_at),
            })}
          </Typography.Text>
        </Space>
      )}
      {task && (
        <Collapse
          items={[
            {
              key: "events",
              label: t("execution.tools"),
              children: (
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    maxHeight: 280,
                    overflow: "auto",
                  }}
                >
                  {JSON.stringify(
                    task.events.filter((event) =>
                      [
                        "tool",
                        "tool_start",
                        "tool_end",
                        "tool_result",
                        "attachment",
                      ].includes(event.type),
                    ),
                    null,
                    2,
                  )}
                </pre>
              ),
            },
          ]}
        />
      )}
      {task?.error && (
        <Alert
          type="error"
          message={
            task.error === "TASK_TIMEOUT"
              ? t("execution.timeout")
              : t("execution.failed")
          }
        />
      )}
      <List
        dataSource={task?.messages || []}
        renderItem={(item) => (
          <List.Item style={{ whiteSpace: "pre-wrap" }}>
            <div>
              <Tag color={item.phase === "summary" ? "green" : undefined}>
                {item.phase === "summary"
                  ? t("engineHub.finalResult")
                  : item.engine || item.role}
              </Tag>
              {item.content}
            </div>
          </List.Item>
        )}
      />
      {active && (
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
          {task?.events
            .slice(
              task.events
                .map((event) => event.type)
                .lastIndexOf("step_started") + 1,
            )
            .filter(
              (event) => event.type === "token" && event.step === task.step,
            )
            .map((event) => event.content || "")
            .join("")}
        </Typography.Paragraph>
      )}
      {task?.permission && (
        <Card size="small" title={t("execution.permission")}>
          {task.steps[task.step].engine === "native" && (
            <Space direction="vertical" style={{ width: "100%" }}>
              <Input.TextArea
                value={permissionText}
                onChange={(event) => setPermissionText(event.target.value)}
                placeholder={t("execution.answerQuestion")}
              />
              <Button
                disabled={busy || !permissionText.trim()}
                onClick={() =>
                  void act(() =>
                    executionApi.answer(task.id, task.permission!.id, {
                      decision: "respond",
                      message: permissionText,
                    }),
                  )
                }
              >
                {t("execution.submitAnswer")}
              </Button>
            </Space>
          )}
          <pre
            style={{ whiteSpace: "pre-wrap", maxHeight: 220, overflow: "auto" }}
          >
            {JSON.stringify(task.permission.request, null, 2)}
          </pre>
          <Space wrap>
            {task.steps[task.step].engine === "dsh"
              ? task.permission.request.options?.map((option) => (
                  <Button
                    key={option.option_id}
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        executionApi.answer(task.id, task.permission!.id, {
                          option_id: option.option_id,
                        }),
                      )
                    }
                  >
                    {option.name}
                  </Button>
                ))
              : ["approve", "reject"].map((decision) => (
                  <Button
                    key={decision}
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        executionApi.answer(task.id, task.permission!.id, {
                          decision,
                        }),
                      )
                    }
                  >
                    {t(`execution.${decision}`)}
                  </Button>
                ))}
          </Space>
        </Card>
      )}
      <Input.TextArea
        value={text}
        maxLength={16000}
        onChange={(event) => setText(event.target.value)}
        placeholder={t("execution.input")}
        disabled={active || busy}
      />
      <Space wrap>
        <Button
          type="primary"
          disabled={active || !ready || !text.trim()}
          loading={busy}
          onClick={() => void send(text)}
        >
          {t("execution.send")}
        </Button>
        <Button
          disabled={active || busy || !voice.service}
          onClick={() => {
            stop();
            void voice.record();
          }}
        >
          {t("execution.record")}
        </Button>
        <Button
          onClick={() => {
            voice.stop();
            stop();
          }}
        >
          {t("voiceLibrary.stop")}
        </Button>
        {active && task && (
          <Button
            danger
            onClick={() => void act(() => executionApi.cancel(task.id))}
          >
            {t("execution.cancel")}
          </Button>
        )}
        {task && (
          <Button onClick={() => setExportOpen(true)}>
            {t("execution.export")}
          </Button>
        )}
      </Space>
      {task && (
        <ConversationExport
          agent=""
          thread={task.id}
          engine="execution"
          open={exportOpen}
          onClose={() => setExportOpen(false)}
        />
      )}
    </Space>
  );
}
