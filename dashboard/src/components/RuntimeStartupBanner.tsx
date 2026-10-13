import { Alert } from "antd";
import { useTranslation } from "react-i18next";
import { useRuntimeStartup } from "../context/RuntimeStartupContext";

export default function RuntimeStartupBanner() {
  const { t } = useTranslation();
  const { preparing, failed, unreachable } = useRuntimeStartup();
  if (!preparing && !failed && !unreachable) return null;
  return (
    <Alert
      banner
      showIcon
      type={failed || unreachable ? "warning" : "info"}
      message={t(
        failed
          ? "startup.runtimeFailed"
          : unreachable
          ? "startup.runtimeUnreachable"
          : "startup.runtimePreparing",
      )}
      style={{ flexShrink: 0 }}
    />
  );
}
