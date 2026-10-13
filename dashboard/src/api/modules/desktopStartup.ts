export interface DesktopStartupStatus {
  application: "xfriend";
  protocol: 1;
  state: "starting" | "ready" | "failed";
  phase: string;
  message?: string;
  elapsed_ms: number;
}

/** This probe belongs to the local shell, before setup, login or Python exist. */
export async function readDesktopStartup(
  signal: AbortSignal,
): Promise<DesktopStartupStatus> {
  const response = await fetch("/api/desktop/startup", {
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error("Desktop startup probe failed");
  const value = (await response.json()) as Partial<DesktopStartupStatus>;
  if (
    value.application !== "xfriend" ||
    value.protocol !== 1 ||
    !["starting", "ready", "failed"].includes(value.state ?? "") ||
    typeof value.phase !== "string" ||
    typeof value.elapsed_ms !== "number" ||
    !Number.isFinite(value.elapsed_ms)
  ) {
    throw new Error("Unexpected desktop startup protocol");
  }
  return value as DesktopStartupStatus;
}
