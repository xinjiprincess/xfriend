import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { healthApi, type RuntimeReadiness } from "../api/modules/health";
import {
  RuntimeStartupProvider,
  useRuntimeStartup,
} from "./RuntimeStartupContext";
import RuntimeStartupBanner from "../components/RuntimeStartupBanner";

const { refresh } = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("./AgentContext", () => ({ useAgent: () => ({ refresh }) }));
vi.mock("../api/modules/health", () => ({ healthApi: { readiness: vi.fn() } }));
const waiting: RuntimeReadiness = {
  ready: false,
  core_ready: true,
  runtime_ready: false,
  runtime_failed: false,
  agents_ready: false,
};
const ready = {
  ...waiting,
  ready: true,
  runtime_ready: true,
  agents_ready: true,
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
});
afterEach(() => vi.useRealTimers());

function Workspace() {
  const { runtimeReady } = useRuntimeStartup();
  return (
    <>
      <RuntimeStartupBanner />
      <div>saved conversation</div>
      <button disabled={!runtimeReady}>send task</button>
    </>
  );
}

describe("background expert preparation", () => {
  it("keeps history visible and enables sending only after preparation", async () => {
    vi.mocked(healthApi.readiness)
      .mockResolvedValueOnce(waiting)
      .mockResolvedValue(ready);
    render(
      <RuntimeStartupProvider>
        <Workspace />
      </RuntimeStartupProvider>,
    );
    await act(async () => {});
    expect(screen.getByText("saved conversation")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "send task" })).toBeDisabled();
    expect(screen.getByText("startup.runtimePreparing")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByRole("button", { name: "send task" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(healthApi.readiness).toHaveBeenCalledTimes(3);
  });

  it("shows preparation failure while retaining the readable workspace", async () => {
    vi.mocked(healthApi.readiness).mockResolvedValue({
      ...waiting,
      runtime_failed: true,
    });
    render(
      <RuntimeStartupProvider>
        <Workspace />
      </RuntimeStartupProvider>,
    );
    await act(async () => {});
    expect(screen.getByText("startup.runtimeFailed")).toBeInTheDocument();
    expect(screen.getByText("saved conversation")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "send task" })).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(healthApi.readiness).toHaveBeenCalledTimes(2);
  });

  it("recovers from an unavailable status endpoint without remounting content", async () => {
    vi.mocked(healthApi.readiness)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(ready);
    render(
      <RuntimeStartupProvider>
        <Workspace />
      </RuntimeStartupProvider>,
    );
    await act(async () => {});
    const content = screen.getByText("saved conversation");
    expect(screen.getByText("startup.runtimeUnreachable")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByText("saved conversation")).toBe(content);
    expect(screen.getByRole("button", { name: "send task" })).toBeEnabled();
  });

  it("detects a lost listener after startup and recovers without discarding the workspace", async () => {
    vi.mocked(healthApi.readiness)
      .mockResolvedValueOnce(ready)
      .mockRejectedValueOnce(new Error("503"))
      .mockResolvedValue(ready);
    render(
      <RuntimeStartupProvider>
        <Workspace />
      </RuntimeStartupProvider>,
    );
    await act(async () => {});
    const content = screen.getByText("saved conversation");
    expect(screen.getByRole("button", { name: "send task" })).toBeEnabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByText("startup.runtimeUnreachable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "send task" })).toBeDisabled();
    expect(screen.getByText("saved conversation")).toBe(content);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(screen.getByRole("button", { name: "send task" })).toBeEnabled();
    expect(screen.getByText("saved conversation")).toBe(content);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
