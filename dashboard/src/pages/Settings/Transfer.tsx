import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Input,
  Select,
  Space,
  Spin,
  Table,
  Typography,
} from "antd";
import PageShell from "../../layouts/PageShell";
import { transferApi } from "../../api/modules/transfer";
import type {
  ImportChoice,
  ImportResult,
  Inspection,
  TransferInventory,
  TransferJob,
} from "../../api/modules/transfer";

export default function TransferPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const [inventory, setInventory] = useState<TransferInventory>();
  const [agents, setAgents] = useState<string[]>(
    params.get("agent") ? [params.get("agent")!] : [],
  );
  const [providers, setProviders] = useState<number[]>([]);
  const [voices, setVoices] = useState<number[]>([]);
  const [skills, setSkills] = useState(true);
  const [secrets, setSecrets] = useState(false);
  const [exportPassword, setExportPassword] = useState("");
  const [file, setFile] = useState<File>();
  const [importPassword, setImportPassword] = useState("");
  const [inspection, setInspection] = useState<Inspection>();
  const [choices, setChoices] = useState<Record<string, ImportChoice>>({});
  const [trust, setTrust] = useState(false);
  const [revision, setRevision] = useState("");
  const [result, setResult] = useState<ImportResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [readyExport, setReadyExport] = useState<TransferJob>();
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    transferApi
      .inventory()
      .then((data) => {
        if (mounted.current) setInventory(data);
      })
      .catch(() => {
        if (mounted.current) setError(t("portableTransfer.failed"));
      });
    return () => {
      mounted.current = false;
    };
  }, [t]);

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (failure) {
      if (mounted.current)
        setError(
          failure instanceof Error
            ? failure.message
            : t("portableTransfer.failed"),
        );
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  const change = (ref: string, value: string) => {
    setRevision("");
    setChoices((old) => ({
      ...old,
      [ref]: value.startsWith("reuse:")
        ? { action: "reuse", target: value.slice(6) }
        : { action: value as "create" | "skip" },
    }));
  };
  const exportPack = () =>
    run(async () => {
      setReadyExport(undefined);
      const created = await transferApi.export({
        agent_ids: agents,
        provider_ids: providers,
        voice_provider_ids: voices,
        include_skills: skills,
        include_secrets: secrets,
        password: exportPassword,
      });
      setExportPassword("");
      setNotice(t("portableTransfer.exporting"));
      const deadline = Date.now() + 180000;
      while (mounted.current && Date.now() < deadline) {
        const job = await transferApi.job(created.id);
        if (job.state === "failed")
          throw new Error(job.message || t("portableTransfer.failed"));
        if (job.state === "completed") {
          setReadyExport(job);
          setNotice(t("portableTransfer.reviewBeforeDownload"));
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      if (mounted.current) throw new Error(t("portableTransfer.timeout"));
    });
  const downloadPack = () =>
    run(async () => {
      if (!readyExport) return;
      const blob = await transferApi.download(readyExport.id);
      if (!mounted.current) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "Xfriend-config.xfpack";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(t("portableTransfer.exported"));
    });
  const inspect = () =>
    run(async () => {
      if (!file) return;
      if (file.size > 72 * 1024 * 1024)
        throw new Error(t("portableTransfer.tooLarge"));
      if (inspection && !result) await transferApi.cancel(inspection.id);
      setInspection(undefined);
      setResult(undefined);
      setRevision("");
      const preview = await transferApi.inspect(file, importPassword);
      setChoices(
        Object.fromEntries(
          preview.package.providers.map((p) => [
            p.ref,
            { action: inventory?.can_manage_models ? "create" : "skip" },
          ]),
        ),
      );
      setTrust(false);
      setInspection(preview);
    });
  const commit = () =>
    run(async () => {
      if (!inspection) return;
      let current = revision;
      if (!current) {
        current = (await transferApi.plan(inspection.id, choices, trust))
          .revision;
        setRevision(current);
      }
      const imported = await transferApi.commit(
        inspection.id,
        current,
        importPassword,
      );
      setResult(imported);
      setImportPassword("");
      setInventory(await transferApi.inventory());
    });
  const cancel = () =>
    run(async () => {
      if (inspection) await transferApi.cancel(inspection.id);
      setInspection(undefined);
      setRevision("");
      setImportPassword("");
    });
  const resources = inspection
    ? [
        ...inspection.package.providers.map((p) => ({
          ...p,
          type: p.family,
          resource: "provider",
        })),
        ...inspection.package.experts.map((e) => ({
          ...e,
          type: e.kind,
          resource: "expert",
        })),
      ]
    : [];

  return (
    <PageShell
      title={t("portableTransfer.title")}
      subtitle={t("portableTransfer.subtitle")}
    >
      <Space
        direction="vertical"
        size="large"
        style={{ width: "100%", maxWidth: 1000 }}
      >
        {error && <Alert type="error" showIcon message={error} />}
        {notice && <Alert type="info" showIcon message={notice} />}
        <Alert type="info" showIcon message={t("portableTransfer.scope")} />
        {!inventory ? (
          <Spin />
        ) : (
          <>
            <Card title={t("portableTransfer.exportTitle")}>
              <Space
                direction="vertical"
                size="middle"
                style={{ width: "100%" }}
              >
                <Typography.Text>
                  {t("portableTransfer.agents")}
                </Typography.Text>
                <Select
                  aria-label={t("portableTransfer.agents")}
                  mode="multiple"
                  style={{ width: "100%" }}
                  value={agents}
                  onChange={setAgents}
                  disabled={busy}
                  options={inventory.agents.map((a) => ({
                    value: a.id,
                    label: `${a.name} (${t(`portableTransfer.${a.kind}`)})`,
                  }))}
                />
                {inventory.can_manage_models && (
                  <>
                    <Typography.Text>
                      {t("portableTransfer.models")}
                    </Typography.Text>
                    <Select
                      aria-label={t("portableTransfer.models")}
                      mode="multiple"
                      style={{ width: "100%" }}
                      value={providers}
                      onChange={setProviders}
                      disabled={busy}
                      options={inventory.providers.map((p) => ({
                        value: p.id,
                        label: p.name,
                      }))}
                    />
                    <Typography.Text>
                      {t("portableTransfer.voice")}
                    </Typography.Text>
                    <Select
                      aria-label={t("portableTransfer.voice")}
                      mode="multiple"
                      style={{ width: "100%" }}
                      value={voices}
                      onChange={setVoices}
                      disabled={busy}
                      options={inventory.voice_providers.map((p) => ({
                        value: p.id,
                        label: p.name,
                      }))}
                    />
                  </>
                )}
                <Checkbox
                  checked={skills}
                  disabled={busy}
                  onChange={(event) => setSkills(event.target.checked)}
                >
                  {t("portableTransfer.skills")}
                </Checkbox>
                <Checkbox
                  checked={secrets}
                  disabled={busy || !inventory.can_manage_models}
                  onChange={(event) => setSecrets(event.target.checked)}
                >
                  {t("portableTransfer.secrets")}
                </Checkbox>
                <Input.Password
                  aria-label={t("portableTransfer.exportPassword")}
                  placeholder={t("portableTransfer.exportPassword")}
                  autoComplete="new-password"
                  value={exportPassword}
                  onChange={(event) => setExportPassword(event.target.value)}
                  disabled={busy}
                />
                <Button
                  type="primary"
                  onClick={exportPack}
                  loading={busy}
                  disabled={
                    (!agents.length && !providers.length && !voices.length) ||
                    (secrets && !exportPassword)
                  }
                >
                  {t("portableTransfer.exportButton")}
                </Button>
                {readyExport && (
                  <>
                    <Alert
                      type="warning"
                      showIcon
                      message={t("portableTransfer.reviewBeforeDownload")}
                      description={readyExport.warnings?.join(" ")}
                    />
                    <Collapse
                      items={(readyExport.preview || []).map((item, index) => ({
                        key: String(index),
                        label: `${item.expert} / ${item.path} (${item.size} B)${
                          item.possible_secret
                            ? ` ⚠ ${t("portableTransfer.possibleSecret")}`
                            : ""
                        }`,
                        children: (
                          <pre
                            style={{
                              whiteSpace: "pre-wrap",
                              overflowWrap: "anywhere",
                              maxHeight: 240,
                              overflow: "auto",
                            }}
                          >
                            {item.sample}
                          </pre>
                        ),
                      }))}
                    />
                    <Button onClick={downloadPack} loading={busy}>
                      {t("portableTransfer.downloadReviewed")}
                    </Button>
                  </>
                )}
              </Space>
            </Card>
            <Card title={t("portableTransfer.importTitle")}>
              <Space
                direction="vertical"
                size="middle"
                style={{ width: "100%" }}
              >
                <input
                  type="file"
                  accept=".xfpack"
                  aria-label={t("portableTransfer.file")}
                  disabled={busy || (!!inspection && !result)}
                  onChange={(event) => {
                    setFile(event.target.files?.[0]);
                  }}
                />
                <Input.Password
                  aria-label={t("portableTransfer.importPassword")}
                  placeholder={t("portableTransfer.importPassword")}
                  autoComplete="off"
                  value={importPassword}
                  disabled={busy || !!result}
                  onChange={(event) => setImportPassword(event.target.value)}
                />
                <Button onClick={inspect} disabled={!file || busy}>
                  {t("portableTransfer.preview")}
                </Button>
                {inspection && !result && (
                  <>
                    <Alert
                      type="warning"
                      showIcon
                      message={t("portableTransfer.previewHint")}
                      description={inspection.package.warnings.join(" ")}
                    />
                    <Table
                      rowKey="ref"
                      pagination={false}
                      dataSource={resources}
                      columns={[
                        {
                          title: t("portableTransfer.name"),
                          dataIndex: "name",
                        },
                        {
                          title: t("portableTransfer.type"),
                          dataIndex: "type",
                          render: (value: string) =>
                            t(`portableTransfer.${value}`),
                        },
                        {
                          title: t("portableTransfer.action"),
                          render: (_, row) => {
                            const choice = choices[row.ref] || {
                              action: "create",
                            };
                            const targets =
                              row.resource === "expert"
                                ? inventory.agents.filter(
                                    (a) => a.kind === row.type,
                                  )
                                : row.type === "text"
                                ? inventory.providers
                                : inventory.voice_providers;
                            return (
                              <Select
                                aria-label={`${t("portableTransfer.action")} ${
                                  row.name
                                }`}
                                style={{ minWidth: 200 }}
                                disabled={busy}
                                value={
                                  choice.action === "reuse"
                                    ? `reuse:${choice.target}`
                                    : choice.action
                                }
                                onChange={(value) => change(row.ref, value)}
                                options={[
                                  ...(row.resource === "expert" ||
                                  inventory.can_manage_models
                                    ? [
                                        {
                                          value: "create",
                                          label: t("portableTransfer.create"),
                                        },
                                      ]
                                    : []),
                                  {
                                    value: "skip",
                                    label: t("portableTransfer.skip"),
                                  },
                                  ...targets.map((target) => ({
                                    value: `reuse:${target.id}`,
                                    label: `${t("portableTransfer.reuse")} ${
                                      target.name
                                    }`,
                                  })),
                                ]}
                              />
                            );
                          },
                        },
                      ]}
                    />
                    <Checkbox
                      checked={trust}
                      disabled={busy}
                      onChange={(event) => {
                        setTrust(event.target.checked);
                        setRevision("");
                      }}
                    >
                      {t("portableTransfer.trustSkills")}
                    </Checkbox>
                    <Space>
                      <Button type="primary" onClick={commit} loading={busy}>
                        {t("portableTransfer.confirm")}
                      </Button>
                      <Button onClick={cancel} disabled={busy}>
                        {t("portableTransfer.cancel")}
                      </Button>
                    </Space>
                  </>
                )}
                {result && (
                  <>
                    <Alert
                      type={result.pending.length ? "warning" : "success"}
                      showIcon
                      message={t("portableTransfer.imported")}
                      description={t("portableTransfer.counts", {
                        agents: result.created_agents.length,
                        providers: result.created_providers,
                      })}
                    />
                    {result.pending.length > 0 && (
                      <ul>
                        {result.pending.map((p, index) => (
                          <li key={index}>
                            {inspection?.package.experts.find(
                              (e) => e.ref === p.ref,
                            )?.name ||
                              inspection?.package.providers.find(
                                (provider) => provider.ref === p.ref,
                              )?.name ||
                              p.ref}
                            :{" "}
                            {t(`portableTransfer.pending.${p.reason}`, {
                              defaultValue: t("portableTransfer.dependency"),
                            })}
                          </li>
                        ))}
                      </ul>
                    )}
                    <Button href="/experts">
                      {t("portableTransfer.openExperts")}
                    </Button>
                  </>
                )}
              </Space>
            </Card>
          </>
        )}
      </Space>
    </PageShell>
  );
}
