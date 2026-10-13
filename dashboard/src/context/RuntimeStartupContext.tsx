import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { healthApi, type RuntimeReadiness } from "../api/modules/health";
import { useAgent } from "./AgentContext";

const RuntimeStartupContext = createContext({
  runtimeReady: true,
  preparing: false,
  failed: false,
  unreachable: false,
});

export function RuntimeStartupProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<RuntimeReadiness>();
  const [unreachable, setUnreachable] = useState(false);
  const { refresh } = useAgent();
  useEffect(() => {
    let disposed = false;
    let retry: ReturnType<typeof setTimeout>;
    let request: AbortController | undefined;
    let wasPreparing = false;
    const poll = async () => {
      request = new AbortController();
      const deadline = setTimeout(() => request?.abort(), 2500);
      let complete = false;
      try {
        const next = await healthApi.readiness(request.signal);
        if (disposed) return;
        setSnapshot(next);
        setUnreachable(false);
        complete = next.agents_ready || next.ready || next.runtime_failed;
        if (!complete || wasPreparing) void refresh({ silent: true });
        wasPreparing = !complete;
      } catch {
        if (!disposed) {
          setUnreachable(true);
          wasPreparing = true;
        }
      } finally {
        clearTimeout(deadline);
        if (!disposed)
          retry = setTimeout(() => void poll(), complete ? 5000 : 1500);
      }
    };
    void poll();
    return () => {
      disposed = true;
      clearTimeout(retry);
      request?.abort();
    };
  }, [refresh]);
  return (
    <RuntimeStartupContext.Provider
      value={{
        runtimeReady:
          !unreachable && Boolean(snapshot?.runtime_ready || snapshot?.ready),
        preparing: Boolean(
          snapshot &&
            !snapshot.agents_ready &&
            !snapshot.ready &&
            !snapshot.runtime_failed,
        ),
        failed: Boolean(snapshot?.runtime_failed),
        unreachable,
      }}
    >
      {children}
    </RuntimeStartupContext.Provider>
  );
}

export const useRuntimeStartup = () => useContext(RuntimeStartupContext);
