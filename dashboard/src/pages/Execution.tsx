import PageShell from "../layouts/PageShell";
import ExecutionConsole from "../components/ExecutionConsole";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import ExpertTemplates from "../components/ExpertTemplates";

export default function Execution() {
  const { t } = useTranslation();
  const [revision, setRevision] = useState(0);
  return (
    <PageShell title={t("execution.title")} subtitle={t("execution.subtitle")}>
      <ExpertTemplates onCreated={() => setRevision((value) => value + 1)} />
      <ExecutionConsole key={revision} />
    </PageShell>
  );
}
