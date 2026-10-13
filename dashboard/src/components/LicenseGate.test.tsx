import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import LicenseGate from "./LicenseGate";
import { licensingApi, type ProductLicense } from "../api/modules/licensing";

vi.mock("../api/modules/licensing", () => ({
  licensingApi: { status: vi.fn(), sync: vi.fn(), activate: vi.fn() },
}));

const trial = (overrides: Partial<ProductLicense> = {}): ProductLicense => ({
  required: true,
  valid: true,
  state: "trial",
  mode: "local_trial",
  trial_days: 5,
  device_id: "test-device",
  edition: "trial",
  expires_at: Math.floor(Date.now() / 1000) + 5 * 86400,
  last_synced_at: null,
  sync_error: null,
  timezone: "Asia/Shanghai",
  ...overrides,
});

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(licensingApi.status).mockResolvedValue(trial());
  vi.mocked(licensingApi.sync).mockResolvedValue(trial());
});
afterEach(() => vi.useRealTimers());

function show(path = "/chat") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LicenseGate>
        <div>user-workspace</div>
      </LicenseGate>
    </MemoryRouter>,
  );
}

describe("five-day desktop trial", () => {
  it("enters the workspace automatically and exposes the remaining trial", async () => {
    show();
    expect(await screen.findByText("user-workspace")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "licensing.remaining" }),
    ).toBeInTheDocument();
    expect(licensingApi.activate).not.toHaveBeenCalled();
  });

  it("shows local expiry without an online activation-code form", async () => {
    show("/activation");
    expect(await screen.findByText("licensing.localTitle")).toBeInTheDocument();
    expect(screen.getByText("licensing.localDescription")).toBeInTheDocument();
    expect(screen.getByText("licensing.expires")).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("licensing.code"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "licensing.sync" }),
    ).not.toBeInTheDocument();
  });

  it("blocks new use after expiry and permits a local recheck", async () => {
    const expired = trial({ valid: false, state: "expired" });
    vi.mocked(licensingApi.status).mockResolvedValue(expired);
    vi.mocked(licensingApi.sync).mockResolvedValue(expired);
    vi.mocked(licensingApi.activate).mockResolvedValue(expired);
    show();
    expect(
      await screen.findByText("licensing.localStates.expired"),
    ).toBeInTheDocument();
    expect(screen.queryByText("user-workspace")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "licensing.recheck" }));
    await act(async () => {});
    expect(licensingApi.activate).toHaveBeenCalledWith(undefined);
    expect(
      screen.queryByRole("button", { name: "licensing.enter" }),
    ).not.toBeInTheDocument();
  });

  it("checks a running session again when the trial expires", async () => {
    vi.useFakeTimers();
    vi.mocked(licensingApi.sync)
      .mockResolvedValueOnce(trial())
      .mockResolvedValue(trial({ valid: false, state: "expired" }));
    show();
    await act(async () => {});
    expect(screen.getByText("user-workspace")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(screen.queryByText("user-workspace")).not.toBeInTheDocument();
    expect(
      screen.getByText("licensing.localStates.expired"),
    ).toBeInTheDocument();
  });

  it("retains server activation for a server-licensed build", async () => {
    const server = trial({ mode: "server", trial_days: null });
    vi.mocked(licensingApi.status).mockResolvedValue(server);
    vi.mocked(licensingApi.sync).mockResolvedValue(server);
    show("/activation");
    expect(
      await screen.findByPlaceholderText("licensing.code"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "licensing.redeem" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("licensing.localDescription"),
    ).not.toBeInTheDocument();
  });
});

describe("online device license", () => {
  const online = (overrides: Partial<ProductLicense> = {}) =>
    trial({ mode: "server", trial_days: null, ...overrides });

  it("claims a new computer's trial automatically and shows the remaining days", async () => {
    vi.mocked(licensingApi.status).mockResolvedValue(
      online({
        valid: false,
        state: "unactivated",
        edition: null,
        expires_at: null,
      }),
    );
    vi.mocked(licensingApi.sync).mockResolvedValue(online());
    show();
    expect(await screen.findByText("user-workspace")).toBeInTheDocument();
    expect(licensingApi.sync).toHaveBeenCalledTimes(1);
    expect(licensingApi.activate).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "licensing.remaining" }),
    ).toBeInTheDocument();
  });

  it("keeps the first launch blocked when the activation service is unavailable", async () => {
    const unavailable = online({
      valid: false,
      state: "unactivated",
      edition: null,
      expires_at: null,
      sync_error: "unavailable",
    });
    vi.mocked(licensingApi.status).mockResolvedValue(unavailable);
    vi.mocked(licensingApi.sync).mockResolvedValue(unavailable);
    show();
    expect(
      await screen.findByText("licensing.errors.unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("user-workspace")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "licensing.enter" }),
    ).not.toBeInTheDocument();
  });

  it("continues with a verified license when the server is offline", async () => {
    vi.mocked(licensingApi.status).mockResolvedValue(online());
    vi.mocked(licensingApi.sync).mockResolvedValue(
      online({ sync_error: "unavailable" }),
    );
    show();
    await act(async () => {});
    expect(screen.getByText("user-workspace")).toBeInTheDocument();
  });

  it("synchronizes an administrator renewal without reinstalling", async () => {
    const expired = online({ valid: false, state: "expired" });
    vi.mocked(licensingApi.status).mockResolvedValue(expired);
    vi.mocked(licensingApi.sync).mockResolvedValue(expired);
    vi.mocked(licensingApi.activate).mockResolvedValue(
      online({ state: "full", edition: "full" }),
    );
    show("/activation");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "licensing.sync" }));
    expect(
      await screen.findByRole("button", { name: "licensing.enter" }),
    ).toBeInTheDocument();
    expect(licensingApi.activate).toHaveBeenCalledWith(undefined);
  });

  it("redeems a code and retains the failure message until a retry succeeds", async () => {
    vi.mocked(licensingApi.status).mockResolvedValue(online());
    vi.mocked(licensingApi.sync).mockResolvedValue(online());
    vi.mocked(licensingApi.activate)
      .mockResolvedValueOnce(online({ sync_error: "code_rejected" }))
      .mockResolvedValueOnce(online({ state: "full", edition: "full" }));
    show("/activation");
    await act(async () => {});
    const input = screen.getByPlaceholderText("licensing.code");
    fireEvent.change(input, { target: { value: "test-activation-code" } });
    fireEvent.click(screen.getByRole("button", { name: "licensing.redeem" }));
    expect(
      await screen.findByText("licensing.errors.code_rejected"),
    ).toBeInTheDocument();
    expect(input).toHaveValue("test-activation-code");
    fireEvent.click(screen.getByRole("button", { name: "licensing.redeem" }));
    await act(async () => {});
    expect(input).toHaveValue("");
    expect(licensingApi.activate).toHaveBeenCalledWith("test-activation-code");
    expect(
      screen.queryByText("licensing.errors.code_rejected"),
    ).not.toBeInTheDocument();
  });
});
