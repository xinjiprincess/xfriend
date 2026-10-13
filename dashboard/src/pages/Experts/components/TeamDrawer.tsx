import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Spin,
} from "antd";
import { message } from "@/utils/antdMessage";

import {
  teamsApi,
  type TeamRecord,
  type TeamTemplateFile,
} from "../../../api/modules/teams";
import type { OctopAgent } from "../../../context/AgentContext";
import { useAgentFormResources } from "../../../hooks/useAgentFormResources";
import { apiErrorMessage } from "../../../utils/apiError";
import {
  buildModelSelectOptions,
  defaultModelFromForm,
  defaultModelToForm,
  MODEL_AUTO_VALUE,
} from "../../../utils/modelOptions";
import { TEAM_AVATAR_URL, TEAM_ICON_NAME } from "../../../utils/teamAgent";
import { metaForFile } from "./iconForName";
import TeamMemberPicker, {
  mergeTeamPickerExperts,
  selectedRosterIds,
  type TeamMemberOption,
} from "./TeamMemberPicker";
import styles from "../index.module.less";

interface TeamDrawerProps {
  open: boolean;
  mode: "create" | "edit";
  team?: OctopAgent | null;
  experts: OctopAgent[];
  onClose: () => void;
  onSaved: (team: TeamRecord) => void;
}

interface TeamFormValues {
  name: string;
  description?: string;
  default_model: string;
  welcome_message?: string;
  member_ids: string[];
  system_prompt?: string;
  member_models?: Record<string, string>;
}

export default function TeamDrawer({
  open,
  mode,
  team,
  experts,
  onClose,
  onSaved,
}: TeamDrawerProps) {
  const { t } = useTranslation();
  const [form] = Form.useForm<TeamFormValues>();
  const { models, modelsLoading } = useAgentFormResources(open);
  const [submitting, setSubmitting] = useState(false);
  const [templateFiles, setTemplateFiles] = useState<TeamTemplateFile[]>([]);
  const [templateLoading, setTemplateLoading] = useState(false);
  const [rosterExtras, setRosterExtras] = useState<TeamMemberOption[]>([]);
  const [revision, setRevision] = useState<string>();
  const [recordLoading, setRecordLoading] = useState(false);
  const [recordError, setRecordError] = useState(false);
  const [sourceTeams, setSourceTeams] = useState<TeamRecord[]>([]);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sourceId, setSourceId] = useState<string>();
  const [sourceMembers, setSourceMembers] = useState<string[]>([]);
  const [copyModels, setCopyModels] = useState(false);
  const selectedMembers = Form.useWatch("member_ids", form) ?? [];
  const pickerExperts = useMemo(
    () => mergeTeamPickerExperts(experts, rosterExtras),
    [experts, rosterExtras],
  );

  useEffect(() => {
    if (!open) {
      setRosterExtras([]);
      setSourceOpen(false);
      setRevision(undefined);
      return;
    }
    if (mode === "edit" && team) {
      setRecordLoading(true);
      setRecordError(false);
      setRevision(undefined);
      form.setFieldsValue({
        name: team.name,
        description: team.description ?? "",
        default_model: defaultModelToForm(team.default_model),
        welcome_message: team.welcome_message ?? "",
        member_ids: team.member_ids ?? [],
      });
      let cancelled = false;
      void teamsApi
        .get(team.agent_id)
        .then((record) => {
          if (cancelled) return;
          form.setFieldsValue({
            name: record.name,
            description: record.description ?? "",
            default_model: defaultModelToForm(record.default_model),
            welcome_message: record.welcome_message ?? "",
            member_ids: record.members.map((member) => member.agent_id),
            system_prompt: record.system_prompt ?? "",
            member_models: record.member_models ?? {},
          });
          setRevision(record.revision);
          setRosterExtras(
            record.members.map((member) => ({
              agent_id: member.agent_id,
              name: member.name,
              color: member.color,
              icon_name: member.icon_name,
              icon_url: member.icon_url,
              kind: "expert",
              is_shared: member.is_shared,
              is_owner: true,
            })),
          );
        })
        .catch(() => {
          if (!cancelled) {
            setRosterExtras([]);
            setRecordError(true);
          }
        })
        .finally(() => {
          if (!cancelled) setRecordLoading(false);
        });
      return () => {
        cancelled = true;
      };
    }
    setRecordLoading(false);
    setRecordError(false);
    setRevision(undefined);
    form.setFieldsValue({
      name: "",
      description: "",
      default_model: MODEL_AUTO_VALUE,
      welcome_message: t("experts.teams.welcomeDefault"),
      member_ids: [],
      system_prompt: "",
      member_models: {},
    });
    setRosterExtras([]);
  }, [form, mode, open, t, team]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setTemplateLoading(true);
    teamsApi
      .templateFiles()
      .then((files) => {
        if (!cancelled) setTemplateFiles(files);
      })
      .catch(() => {
        if (!cancelled) setTemplateFiles([]);
      })
      .finally(() => {
        if (!cancelled) setTemplateLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const hasNoModels = !modelsLoading && models.length === 0;
  const blocked = submitting || hasNoModels || recordLoading || recordError;

  const handleSubmit = async () => {
    const values = await form.validateFields();
    const body = {
      name: values.name.trim(),
      description: values.description?.trim() || null,
      default_model: defaultModelFromForm(values.default_model),
      icon_name: TEAM_ICON_NAME,
      icon_url: TEAM_AVATAR_URL,
      welcome_message: values.welcome_message?.trim() || null,
      member_ids: selectedRosterIds(values.member_ids, pickerExperts),
      system_prompt: values.system_prompt?.trim() || null,
      member_models: Object.fromEntries(
        Object.entries(values.member_models ?? {}).filter(
          ([id, model]) =>
            values.member_ids.includes(id) &&
            model &&
            model !== MODEL_AUTO_VALUE,
        ),
      ),
      ...(mode === "edit" ? { revision } : {}),
    };
    setSubmitting(true);
    try {
      const saved =
        mode === "edit" && team
          ? await teamsApi.update(team.agent_id, body)
          : await teamsApi.create({
              ...body,
              name: body.name,
              member_ids: body.member_ids,
            });
      message.success(
        mode === "edit"
          ? t("experts.teams.updated")
          : t("experts.teams.created"),
      );
      onSaved(saved);
    } catch (err: unknown) {
      message.error(apiErrorMessage(err, t("experts.createFailed"), t));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      title={
        mode === "edit"
          ? t("experts.teams.editTitle")
          : t("experts.teams.createTitle")
      }
      open={open}
      onClose={onClose}
      width={600}
      destroyOnHidden
      footer={
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className={styles.drawerCancelBtn} onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            className={styles.drawerSaveBtn}
            onClick={() => void handleSubmit()}
            disabled={blocked}
            title={hasNoModels ? t("experts.noModelsWarning") : undefined}
          >
            {submitting
              ? t("experts.creating")
              : mode === "edit"
              ? t("common.save")
              : t("common.create")}
          </button>
        </div>
      }
    >
      {recordError && (
        <Alert
          type="error"
          showIcon
          message={t("experts.teams.loadFailed")}
          style={{ marginBottom: 16 }}
        />
      )}
      {hasNoModels && (
        <Alert
          type="warning"
          showIcon
          message={t("experts.noModelsWarning")}
          action={
            <a href="/admin/models" style={{ whiteSpace: "nowrap" }}>
              {t("experts.goToAdmin")}
            </a>
          }
          style={{ marginBottom: 16 }}
        />
      )}

      <Form form={form} layout="vertical" size="middle">
        <Form.Item
          name="name"
          label={t("experts.agentName")}
          rules={[{ required: true, message: t("experts.pleaseEnterName") }]}
        >
          <Input />
        </Form.Item>
        <Form.Item name="description" label={t("experts.agentDescription")}>
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item
          name="welcome_message"
          label={t("experts.welcomeMessageTitle")}
          extra={
            mode === "edit"
              ? t("experts.editWelcomeHint")
              : t("experts.createWelcomeHint")
          }
        >
          <Input.TextArea
            rows={2}
            placeholder={t("experts.welcomeMessagePlaceholder")}
          />
        </Form.Item>
        <Form.Item name="default_model" label={t("experts.teams.hostModel")}>
          <Select
            loading={modelsLoading}
            options={buildModelSelectOptions(
              models,
              t("experts.defaultModelAuto"),
            )}
            placeholder={t("experts.defaultModelPlaceholder")}
            showSearch
            optionFilterProp="label"
          />
        </Form.Item>
        <Form.Item
          name="system_prompt"
          label={t("experts.teams.hostPrompt")}
          extra={t("experts.teams.hostPromptHint")}
        >
          <Input.TextArea rows={6} maxLength={200000} />
        </Form.Item>
        <Button
          onClick={() =>
            Modal.confirm({
              title: t("experts.teams.resetPrompt"),
              content: t("experts.teams.hostPromptHint"),
              onOk: () => form.setFieldValue("system_prompt", ""),
            })
          }
        >
          {t("experts.teams.resetPrompt")}
        </Button>
        <Form.Item
          name="member_ids"
          label={t("experts.teams.members")}
          extra={t("experts.teams.membersHint")}
          rules={[
            {
              validator: async (_, value: string[]) => {
                if (selectedRosterIds(value, pickerExperts).length < 2) {
                  throw new Error(t("experts.teams.membersMin"));
                }
              },
            },
          ]}
        >
          <TeamMemberPicker experts={pickerExperts} />
        </Form.Item>
        <Button
          onClick={async () => {
            try {
              setSourceTeams(
                (await teamsApi.list()).filter(
                  (item) => item.agent_id !== team?.agent_id,
                ),
              );
              setSourceId(undefined);
              setSourceMembers([]);
              setCopyModels(false);
              setSourceOpen(true);
            } catch (err) {
              message.error(apiErrorMessage(err, t("experts.createFailed"), t));
            }
          }}
        >
          {t("experts.teams.addFromTeam")}
        </Button>
        <Alert
          type="info"
          showIcon
          message={t("experts.teams.memberReferenceHint")}
          style={{ margin: "12px 0" }}
        />
        {selectedMembers.map((id: string) => (
          <Form.Item
            key={id}
            name={["member_models", id]}
            label={t("experts.teams.memberModel", {
              name:
                pickerExperts.find((item) => item.agent_id === id)?.name ?? id,
            })}
          >
            <Select
              loading={modelsLoading}
              options={buildModelSelectOptions(
                models,
                t("experts.teams.inheritModel"),
              )}
              allowClear
              showSearch
              optionFilterProp="label"
            />
          </Form.Item>
        ))}
      </Form>
      <Modal
        open={sourceOpen}
        title={t("experts.teams.addFromTeam")}
        onCancel={() => setSourceOpen(false)}
        onOk={() => {
          const source = sourceTeams.find((item) => item.agent_id === sourceId);
          if (!source) return;
          const existing: string[] = form.getFieldValue("member_ids") ?? [];
          const additions = sourceMembers.filter(
            (id) => !existing.includes(id),
          );
          setRosterExtras((old) =>
            mergeTeamPickerExperts(
              old,
              source.members
                .filter((item) => additions.includes(item.agent_id))
                .map((item) => ({ ...item, kind: "expert", is_owner: true })),
            ),
          );
          const overrides = { ...(form.getFieldValue("member_models") ?? {}) };
          if (copyModels)
            for (const id of additions)
              if (source.member_models?.[id])
                overrides[id] = source.member_models[id];
          form.setFieldsValue({
            member_ids: [...existing, ...additions],
            member_models: overrides,
          });
          setSourceOpen(false);
        }}
        okButtonProps={{ disabled: !sourceMembers.length }}
      >
        <Select
          style={{ width: "100%", marginBottom: 12 }}
          value={sourceId}
          options={sourceTeams.map((item) => ({
            label: item.name,
            value: item.agent_id,
          }))}
          onChange={(id) => {
            setSourceId(id);
            setSourceMembers([]);
          }}
        />
        <Checkbox.Group
          value={sourceMembers}
          onChange={(ids) => setSourceMembers(ids as string[])}
          options={(
            sourceTeams.find((item) => item.agent_id === sourceId)?.members ??
            []
          ).map((item) => ({
            label: item.name,
            value: item.agent_id,
            disabled: selectedMembers.includes(item.agent_id),
          }))}
        />
        <div style={{ marginTop: 12 }}>
          <Checkbox
            checked={copyModels}
            onChange={(event) => setCopyModels(event.target.checked)}
          >
            {t("experts.teams.copyModels")}
          </Checkbox>
        </div>
      </Modal>

      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>
          {t("experts.mdFilesTitle")}
        </div>
        <p
          style={{
            fontSize: 12,
            color: "var(--fn-text-tertiary)",
            margin: "0 0 8px",
          }}
        >
          {t("experts.teams.mdFilesHint")}
        </p>
        {templateLoading ? (
          <div style={{ textAlign: "center", padding: "16px 0" }}>
            <Spin size="small" />
          </div>
        ) : (
          <Collapse
            size="small"
            items={templateFiles.map((file) => {
              const meta = metaForFile(file.name, t);
              return {
                key: file.name,
                label: (
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "baseline",
                      gap: 8,
                    }}
                  >
                    <span style={{ fontWeight: 500 }}>{meta.label}</span>
                    <span
                      style={{
                        fontSize: 11,
                        color:
                          "var(--fn-text-quaternary, var(--fn-text-tertiary))",
                      }}
                    >
                      {file.name}
                    </span>
                  </span>
                ),
                children: (
                  <pre
                    style={{
                      fontSize: 12,
                      maxHeight: 200,
                      overflowY: "auto",
                      background: "var(--fn-bg-secondary, #f5f5f5)",
                      padding: 8,
                      borderRadius: 4,
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {file.content}
                  </pre>
                ),
              };
            })}
          />
        )}
      </div>
    </Drawer>
  );
}
