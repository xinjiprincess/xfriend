import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
} from "antd";
import PageShell from "../../layouts/PageShell";
import { useCloudAccount } from "../../context/CloudAccountContext";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  customerSyncApi,
  type EntityType,
  type SyncEntity,
  type SyncJournal,
  type SyncScope,
} from "../../api/modules/customerSync";
import {
  transferApi,
  type TransferInventory,
} from "../../api/modules/transfer";
import {
  profileFields,
  proposedProfile,
  redactSyncSecret,
  validDraft,
} from "./model";
import "./strings";
import "./style.css";

const fields: Record<string, string[]> = {
  customer: [
    "name",
    "phone",
    "company",
    "role",
    "email",
    "wechat",
    "stage",
    "source",
    "tags",
    "priority",
    "nextContact",
    "notes",
  ],
  communication: ["title", "text", "report", "rawTranscript"],
  opportunity: [
    "title",
    "amount",
    "stage",
    "expectedDate",
    "probabilityLow",
    "probabilityHigh",
    "probabilityReason",
    "probabilityEvidence",
    "notes",
  ],
  followup: ["title", "dueDate", "dueTime", "priority"],
};
const longFields = new Set([
  "notes",
  "text",
  "report",
  "rawTranscript",
  "probabilityReason",
  "probabilityEvidence",
]);
const crmTypes: EntityType[] = [
  "customer",
  "communication",
  "opportunity",
  "followup",
];
const display = (value: unknown) =>
  typeof value === "string" || typeof value === "number" ? String(value) : "";
const name = (entity: SyncEntity) =>
  display(entity.payload.name || entity.payload.title) || entity.entity_id;

export default function CustomersPage() {
  const { t } = useTranslation("customers");
  const account = useCloudAccount()?.status;
  const user = account?.user?.user_id,
    space = account?.space_id;
  if (!user || !space)
    return (
      <PageShell title={t("title")}>
        <Alert type="info" message={t("login")} />
      </PageShell>
    );
  return <CustomerCenter key={`${user}:${space}`} scope={{ user, space }} />;
}

export function CustomerCenter({ scope }: { scope: SyncScope }) {
  const { t } = useTranslation("customers");
  const timezone = useServerTimezone();
  const [journal, setJournal] = useState<SyncJournal>();
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<SyncEntity>();
  const [ai, setAI] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [view, setView] = useState<SyncEntity>();
  const active = useRef(true);
  const working = useRef(false);
  useEffect(() => {
    active.current = true;
    customerSyncApi
      .snapshot({ user: scope.user, space: scope.space })
      .then((j) => {
        if (active.current) setJournal(j);
      })
      .catch(() => {
        if (active.current) setError(t("failed"));
      });
    return () => {
      active.current = false;
    };
  }, [scope.user, scope.space, t]); // identity changes remount the whole center

  const run = async (action: () => Promise<void>) => {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      if (active.current)
        setError(`${t("failed")} ${err instanceof Error ? err.message : ""}`);
      try {
        const state = await customerSyncApi.snapshot(scope);
        if (active.current) setJournal(state);
      } catch {
        /* Keep the visible journal on network failure. */
      }
    } finally {
      working.current = false;
      if (active.current) setBusy(false);
    }
  };
  const update = (state: SyncJournal) => {
    if (active.current) setJournal(state);
  };
  const pending = (e: SyncEntity) =>
    journal?.pending.find(
      (op) => op.entity_id === e.entity_id && op.entity_type === e.entity_type,
    );
  const live = journal?.entities.filter((e) => !e.deleted) || [];
  const customer = live.find(
    (e) => e.entity_type === "customer" && e.entity_id === selected,
  );
  const related = (type: EntityType) =>
    live.filter(
      (e) => e.entity_type === type && e.payload.customerId === selected,
    );
  const openEdit = (entity: SyncEntity, proposal = false) => {
    if (pending(entity)) {
      setError(t("busyEdit"));
      return;
    }
    setAI(proposal);
    setConfirmed(false);
    setDraft(structuredClone(entity));
  };
  const create = (type: EntityType) => {
    const id = crypto.randomUUID();
    openEdit({
      entity_type: type,
      entity_id: id,
      revision: 0,
      deleted: false,
      payload: {
        id,
        ...(type === "customer"
          ? { name: "", insight: {} }
          : { title: "", customerId: selected }),
        ...(type === "communication"
          ? {
              created: Date.now(),
              kind: "text",
              mode: "综合分析",
              attachments: [],
            }
          : {}),
      },
    });
  };
  const editField = (field: string, value: unknown, profile = false) =>
    setDraft((old) =>
      old
        ? {
            ...old,
            payload: {
              ...old.payload,
              ...(profile
                ? {
                    insight: {
                      ...((old.payload.insight as Record<string, unknown>) ||
                        {}),
                      [field]: value,
                    },
                  }
                : { [field]: value }),
            },
          }
        : old,
    );
  const save = () =>
    run(async () => {
      if (!draft || !validDraft(draft)) {
        setError(t("invalid"));
        return;
      }
      update(
        await customerSyncApi.edit(
          scope,
          draft,
          ai ? "ai" : "manual",
          confirmed,
        ),
      );
      if (active.current) {
        if (draft.entity_type === "customer") setSelected(draft.entity_id);
        setDraft(undefined);
        setNotice(t("saved"));
      }
    });
  const badge = (e: SyncEntity) => {
    const op = pending(e);
    return (
      <Tag color={op ? "orange" : "green"}>
        {t(
          op
            ? journal?.conflicts[op.operation_id]
              ? "conflict"
              : "pending"
            : "synced",
        )}
      </Tag>
    );
  };
  const actions = (entity: SyncEntity) => (
    <Space wrap>
      <Button
        disabled={busy || !!pending(entity)}
        onClick={() => openEdit(entity)}
      >
        {t("edit")}
      </Button>
      <Popconfirm
        title={t("confirmDelete")}
        description={
          entity.entity_type === "customer"
            ? t("deleteCustomerDetails")
            : undefined
        }
        okText={t("delete")}
        cancelText={t("cancel")}
        onConfirm={() =>
          run(async () => {
            if (
              entity.entity_type === "customer" &&
              live.some(
                (child) =>
                  ["communication", "followup", "opportunity"].includes(
                    child.entity_type,
                  ) &&
                  child.payload.customerId === entity.entity_id &&
                  pending(child),
              )
            ) {
              setError(t("deleteCustomerPending"));
              return;
            }
            update(
              await customerSyncApi.edit(scope, { ...entity, deleted: true }),
            );
            setNotice(t("saved"));
          })
        }
      >
        <Button danger disabled={busy || !!pending(entity)}>
          {t("delete")}
        </Button>
      </Popconfirm>
    </Space>
  );
  const records = (type: EntityType) => (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Button disabled={busy} onClick={() => create(type)}>
        {t("new")} {t(type)}
      </Button>
      <List
        dataSource={related(type)}
        locale={{ emptyText: t("empty") }}
        renderItem={(entity) => (
          <List.Item>
            <Card
              size="small"
              style={{ width: "100%" }}
              title={
                <Space wrap>
                  {name(entity)}
                  {badge(entity)}
                </Space>
              }
              extra={actions(entity)}
            >
              <Descriptions column={1} size="small">
                {fields[type]
                  .filter(
                    (k) =>
                      k !== "title" &&
                      entity.payload[k] !== undefined &&
                      entity.payload[k] !== "",
                  )
                  .map((key) => (
                    <Descriptions.Item key={key} label={t(key)}>
                      <div className="customer-sync-text">
                        {display(entity.payload[key])}
                      </div>
                    </Descriptions.Item>
                  ))}
                {type === "followup" && (
                  <Descriptions.Item label={t("done")}>
                    <Checkbox
                      checked={entity.payload.done === true}
                      disabled={busy || !!pending(entity)}
                      onChange={(event) =>
                        void run(async () =>
                          update(
                            await customerSyncApi.edit(scope, {
                              ...entity,
                              payload: {
                                ...entity.payload,
                                done: event.target.checked,
                              },
                            }),
                          ),
                        )
                      }
                    />
                  </Descriptions.Item>
                )}
                {type === "communication" &&
                  typeof entity.payload.created === "number" && (
                    <Descriptions.Item label={t("created")}>
                      {formatServerDateTime(
                        entity.payload.created / 1000,
                        timezone,
                      )}
                    </Descriptions.Item>
                  )}
                {type === "communication" &&
                  Array.isArray(entity.payload.attachments) &&
                  entity.payload.attachments.length > 0 && (
                    <Descriptions.Item label={t("attachments")}>
                      {entity.payload.attachments.map(String).join(", ")}
                    </Descriptions.Item>
                  )}
              </Descriptions>
              {type === "communication" && customer && (
                <Button
                  disabled={busy || !!pending(customer)}
                  onClick={() => {
                    const proposal = proposedProfile(customer, entity);
                    if (proposal) openEdit(proposal, true);
                    else setError(t("noAI"));
                  }}
                >
                  {t("reviewAI")}
                </Button>
              )}
            </Card>
          </List.Item>
        )}
      />
    </Space>
  );

  return (
    <PageShell
      title={t("title")}
      subtitle={t("subtitle")}
      actions={
        <Space wrap>
          <Tag>
            {t("pending")}: {journal?.pending.length || 0}
          </Tag>
          <Button
            disabled={busy}
            onClick={() =>
              void run(async () =>
                update(await customerSyncApi.snapshot(scope)),
              )
            }
          >
            {t("refresh")}
          </Button>
          <Button
            type="primary"
            loading={busy}
            onClick={() =>
              void run(async () => {
                update(await customerSyncApi.sync(scope));
                if (active.current) setNotice(t("success"));
              })
            }
          >
            {t("sync")}
          </Button>
        </Space>
      }
    >
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          closable
          onClose={() => setError("")}
        />
      )}
      {notice && <Alert type="info" message={notice} />}
      {!journal ? (
        <Spin />
      ) : (
        <Tabs
          items={[
            {
              key: "crm",
              label: t("customer"),
              children: (
                <div className="customer-sync-grid">
                  <div>
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Input
                        aria-label={t("search")}
                        placeholder={t("search")}
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                      <Button
                        disabled={busy}
                        onClick={() => create("customer")}
                      >
                        {t("new")} {t("customer")}
                      </Button>
                    </Space>
                    <List
                      dataSource={live.filter(
                        (e) =>
                          e.entity_type === "customer" &&
                          [
                            e.payload.name,
                            e.payload.company,
                            e.payload.phone,
                          ].some((value) =>
                            display(value)
                              .toLowerCase()
                              .includes(query.toLowerCase()),
                          ),
                      )}
                      renderItem={(entity) => (
                        <List.Item>
                          <Button
                            type={
                              selected === entity.entity_id ? "primary" : "text"
                            }
                            block
                            onClick={() => setSelected(entity.entity_id)}
                          >
                            {name(entity)} · {display(entity.payload.company)}
                          </Button>
                        </List.Item>
                      )}
                    />
                  </div>
                  <div>
                    {!customer ? (
                      <Empty description={t("select")} />
                    ) : (
                      <>
                        <Card
                          title={
                            <Space>
                              {name(customer)}
                              {badge(customer)}
                            </Space>
                          }
                          extra={actions(customer)}
                        >
                          <Descriptions column={2} size="small">
                            {fields.customer
                              .filter((k) => k !== "name")
                              .map((key) => (
                                <Descriptions.Item key={key} label={t(key)}>
                                  {display(customer.payload[key]) || "—"}
                                </Descriptions.Item>
                              ))}
                          </Descriptions>
                          <Typography.Title level={5}>
                            {t("portrait")}
                          </Typography.Title>
                          <Descriptions column={1}>
                            {profileFields.map((key) => (
                              <Descriptions.Item key={key} label={t(key)}>
                                <div className="customer-sync-text">
                                  {display(
                                    (
                                      customer.payload.insight as
                                        | Record<string, unknown>
                                        | undefined
                                    )?.[key],
                                  ) || "—"}
                                </div>
                              </Descriptions.Item>
                            ))}
                          </Descriptions>
                        </Card>
                        <Tabs
                          items={(
                            [
                              "communication",
                              "followup",
                              "opportunity",
                            ] as const
                          ).map((type) => ({
                            key: type,
                            label: `${t(type)} (${related(type).length})`,
                            children: records(type),
                          }))}
                        />
                      </>
                    )}
                  </div>
                </div>
              ),
            },
            {
              key: "conflicts",
              label: `${t("conflicts")} (${
                Object.keys(journal.conflicts).length
              })`,
              children: (
                <>
                  <Alert type="info" message={t("resolveNotice")} />
                  <List
                    dataSource={Object.entries(journal.conflicts)}
                    renderItem={([id, server]) => {
                      const local = journal.pending.find(
                        (op) => op.operation_id === id,
                      );
                      return (
                        <List.Item>
                          <Card style={{ width: "100%" }} title={name(server)}>
                            <div className="customer-sync-grid">
                              <div>
                                <Typography.Text strong>
                                  {t("localVersion")}
                                </Typography.Text>
                                <pre>
                                  {JSON.stringify(
                                    local?.payload,
                                    redactSyncSecret,
                                    2,
                                  )}
                                </pre>
                                {local?.deleted && <Tag>{t("delete")}</Tag>}
                              </div>
                              <div>
                                <Typography.Text strong>
                                  {t("serverVersion")}
                                </Typography.Text>
                                <pre>
                                  {JSON.stringify(
                                    server.payload,
                                    redactSyncSecret,
                                    2,
                                  )}
                                </pre>
                                {server.deleted && <Tag>{t("delete")}</Tag>}
                              </div>
                            </div>
                            <Space>
                              {(["local", "server"] as const).map((choice) => (
                                <Popconfirm
                                  key={choice}
                                  title={t(choice)}
                                  onConfirm={() =>
                                    run(async () =>
                                      update(
                                        await customerSyncApi.resolve(
                                          scope,
                                          id,
                                          choice,
                                        ),
                                      ),
                                    )
                                  }
                                >
                                  <Button disabled={busy}>{t(choice)}</Button>
                                </Popconfirm>
                              ))}
                            </Space>
                          </Card>
                        </List.Item>
                      );
                    }}
                  />
                </>
              ),
            },
            {
              key: "configuration",
              label: t("configuration"),
              children: (
                <Configuration
                  scope={scope}
                  entities={live.filter(
                    (e) => !crmTypes.includes(e.entity_type),
                  )}
                  busy={busy}
                  run={run}
                  update={update}
                  view={setView}
                />
              ),
            },
          ]}
        />
      )}
      <Modal
        open={!!draft}
        width={760}
        title={
          draft
            ? `${t(ai ? "reviewAI" : "edit")} · ${t(draft.entity_type)}`
            : ""
        }
        onCancel={() => !busy && setDraft(undefined)}
        footer={
          <Space>
            <Button disabled={busy} onClick={() => setDraft(undefined)}>
              {t("cancel")}
            </Button>
            <Button
              type="primary"
              loading={busy}
              disabled={ai && !confirmed}
              onClick={() => void save()}
            >
              {t("save")}
            </Button>
          </Space>
        }
      >
        {ai && <Alert type="warning" message={t("aiNotice")} />}
        {draft && (
          <div className="customer-sync-form">
            {(fields[draft.entity_type] || []).map((field) => (
              <label key={field}>
                {t(field === "title" ? "titleField" : field)}
                {longFields.has(field) ? (
                  <Input.TextArea
                    rows={4}
                    value={display(draft.payload[field])}
                    onChange={(e) => editField(field, e.target.value)}
                  />
                ) : (
                  <Input
                    type={
                      field === "dueDate" || field === "expectedDate"
                        ? "date"
                        : field === "dueTime"
                        ? "time"
                        : "text"
                    }
                    value={display(draft.payload[field])}
                    onChange={(e) => editField(field, e.target.value)}
                  />
                )}
              </label>
            ))}
            {draft.entity_type === "customer" &&
              profileFields.map((field) => (
                <label key={field}>
                  {t(field)}
                  <Input.TextArea
                    rows={2}
                    value={display(
                      (
                        draft.payload.insight as
                          | Record<string, unknown>
                          | undefined
                      )?.[field],
                    )}
                    onChange={(e) => editField(field, e.target.value, true)}
                  />
                </label>
              ))}
            {draft.entity_type === "followup" &&
              ["done", "reminder"].map((field) => (
                <Checkbox
                  key={field}
                  checked={draft.payload[field] === true}
                  onChange={(e) => editField(field, e.target.checked)}
                >
                  {t(field)}
                </Checkbox>
              ))}
          </div>
        )}
        {ai && (
          <Checkbox
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          >
            {t("confirmAI")}
          </Checkbox>
        )}
      </Modal>
      <Modal
        open={!!view}
        title={view ? name(view) : ""}
        onCancel={() => setView(undefined)}
        footer={
          <Button onClick={() => setView(undefined)}>{t("close")}</Button>
        }
      >
        <pre className="customer-sync-text">
          {JSON.stringify(view?.payload, redactSyncSecret, 2)}
        </pre>
      </Modal>
    </PageShell>
  );
}

function Configuration({
  scope,
  entities,
  busy,
  run,
  update,
  view,
}: {
  scope: SyncScope;
  entities: SyncEntity[];
  busy: boolean;
  run: (fn: () => Promise<void>) => Promise<void>;
  update: (journal: SyncJournal) => void;
  view: (e: SyncEntity) => void;
}) {
  const { t } = useTranslation("customers");
  const [inventory, setInventory] = useState<TransferInventory>();
  const [inventoryError, setInventoryError] = useState(false);
  const [agents, setAgents] = useState<string[]>([]);
  const [providers, setProviders] = useState<number[]>([]);
  const [secrets, setSecrets] = useState(false);
  const [kind, setKind] = useState<"experts" | "skills">("experts");
  const [q, setQ] = useState("");
  const [catalog, setCatalog] = useState<
    Awaited<ReturnType<typeof customerSyncApi.catalog>>["items"]
  >([]);
  useEffect(() => {
    let mounted = true;
    transferApi
      .inventory()
      .then((v) => {
        if (mounted) setInventory(v);
      })
      .catch(() => {
        if (mounted) setInventoryError(true);
      });
    return () => {
      mounted = false;
    };
  }, []);
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Alert type="info" message={t("configNotice")} />
      {inventoryError && <Alert type="error" message={t("failed")} />}
      <Select
        aria-label={t("agents")}
        placeholder={t("agents")}
        mode="multiple"
        style={{ width: "100%" }}
        value={agents}
        onChange={setAgents}
        options={inventory?.agents.map((e) => ({ value: e.id, label: e.name }))}
      />
      <Select
        aria-label={t("providers")}
        placeholder={t("providers")}
        mode="multiple"
        style={{ width: "100%" }}
        disabled={!inventory?.can_manage_models}
        value={providers}
        onChange={setProviders}
        options={inventory?.providers.map((p) => ({
          value: p.id,
          label: p.name,
        }))}
      />
      <Checkbox
        disabled={!inventory?.can_manage_models}
        checked={secrets}
        onChange={(e) => setSecrets(e.target.checked)}
      >
        {t("secrets")}
      </Checkbox>
      <Space wrap>
        <Button
          disabled={busy || (!agents.length && !providers.length)}
          onClick={() =>
            void run(async () =>
              update(
                await customerSyncApi.configuration(
                  scope,
                  agents,
                  providers,
                  secrets,
                ),
              ),
            )
          }
        >
          {t("export")}
        </Button>
        <Button
          disabled={busy || !entities.length}
          onClick={() =>
            void run(async () => {
              const blob = await customerSyncApi.package(scope);
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = "customer-sync.xfpack";
              link.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            })
          }
        >
          {t("download")}
        </Button>
        <Link to="/settings/transfer">{t("transfer")}</Link>
      </Space>
      <List
        dataSource={entities}
        renderItem={(e) => (
          <List.Item
            actions={[
              <Button key="view" onClick={() => view(e)}>
                {t("view")}
              </Button>,
            ]}
          >
            <List.Item.Meta title={name(e)} description={t(e.entity_type)} />
          </List.Item>
        )}
      />
      <Typography.Title level={5}>{t("catalog")}</Typography.Title>
      <Space wrap>
        <Select
          value={kind}
          onChange={setKind}
          options={[
            { value: "experts", label: t("experts") },
            { value: "skills", label: t("skills") },
          ]}
        />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label={t("searchCatalog")}
        />
        <Button
          disabled={busy}
          onClick={() =>
            void run(async () =>
              setCatalog((await customerSyncApi.catalog(scope, kind, q)).items),
            )
          }
        >
          {t("searchCatalog")}
        </Button>
      </Space>
      <List
        dataSource={catalog}
        renderItem={(item) => (
          <List.Item
            actions={[
              <Button
                key="add"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const entity_type =
                      item.kind === "skill" ? "skill" : "expert";
                    const existing = entities.find(
                      (e) =>
                        e.entity_id === item.id &&
                        e.entity_type === entity_type,
                    );
                    update(
                      await customerSyncApi.edit(scope, {
                        entity_type,
                        entity_id: item.id,
                        revision: existing?.revision || 0,
                        deleted: false,
                        payload: {
                          ...existing?.payload,
                          id: item.id,
                          name: item.name,
                          ...(entity_type === "expert"
                            ? {
                                prompt: item.content,
                                skills: [],
                                knowledge: [],
                              }
                            : { content: item.content }),
                          catalog: item,
                        },
                      }),
                    );
                  })
                }
              >
                {t("addCatalog")}
              </Button>,
            ]}
          >
            <List.Item.Meta
              title={`${item.name} · ${item.version}`}
              description={item.description}
            />
          </List.Item>
        )}
      />
    </Space>
  );
}
