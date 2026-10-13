import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import StartupGate from "./StartupGate";
import StartupDestination from "./StartupDestination";
import { useStartupDraft } from "../context/StartupDraftContext";
import { readDesktopStartup } from "../api/modules/desktopStartup";

vi.mock("../api/modules/desktopStartup", () => ({
  readDesktopStartup: vi.fn(),
}));
const probe = vi.mocked(readDesktopStartup);
const status = (state: "starting" | "ready" | "failed") => ({
  application: "xfriend" as const,
  protocol: 1 as const,
  state,
  phase: "core",
  elapsed_ms: 1200,
});

beforeEach(() => {
  vi.useFakeTimers();
  probe.mockReset();
  document.documentElement.dataset.xfriendDesktop = "1";
});
afterEach(() => {
  delete document.documentElement.dataset.xfriendDesktop;
  vi.useRealTimers();
});

describe("desktop startup boundary", () => {
  it("shows login immediately while the local service is still starting", async () => {
    probe.mockResolvedValue(status("starting"));
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <StartupGate>
          <div>login form</div>
        </StartupGate>
      </MemoryRouter>,
    );
    expect(screen.getByText("login form")).toBeInTheDocument();
    await act(async () => {});
    expect(screen.getByText("login form")).toBeInTheDocument();
    expect(screen.queryByText("startup.title")).not.toBeInTheDocument();
  });

  it("restores the chosen destination after an intervening login", async () => {
    probe
      .mockResolvedValueOnce(status("starting"))
      .mockResolvedValue(status("ready"));
    function AuthenticatedPage() {
      const location = useLocation();
      const { destination } = useStartupDraft();
      return (
        <div data-testid="destination">
          {location.pathname}:{destination ?? "consumed"}
        </div>
      );
    }
    function LoginBoundary() {
      const [authed, setAuthed] = useState(false);
      const navigate = useNavigate();
      return authed ? (
        <StartupDestination>
          <AuthenticatedPage />
        </StartupDestination>
      ) : (
        <button
          onClick={() => {
            navigate("/chat");
            setAuthed(true);
          }}
        >
          login
        </button>
      );
    }
    render(
      <MemoryRouter>
        <StartupGate>
          <LoginBoundary />
        </StartupGate>
      </MemoryRouter>,
    );
    await act(async () => {});
    fireEvent.click(
      screen.getByRole("button", { name: "startup.nav.engines" }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(750);
    });
    fireEvent.click(screen.getByRole("button", { name: "login" }));
    expect(screen.getByTestId("destination")).toHaveTextContent(
      "/engines:consumed",
    );
  });

  it("keeps auth and activation unmounted, then preserves the draft and chosen route", async () => {
    probe
      .mockResolvedValueOnce(status("starting"))
      .mockResolvedValue(status("ready"));
    const mounted = vi.fn();
    function Business() {
      const { draft } = useStartupDraft();
      const location = useLocation();
      useEffect(mounted, []);
      return (
        <div data-testid="business">
          {location.pathname}:{draft}
        </div>
      );
    }
    render(
      <MemoryRouter>
        <StartupGate>
          <Business />
        </StartupGate>
      </MemoryRouter>,
    );
    await act(async () => {});
    expect(mounted).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "整理项目计划" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "startup.nav.experts" }),
    );
    expect(mounted).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(750);
    });
    expect(screen.getByTestId("business")).toHaveTextContent(
      "/experts:整理项目计划",
    );
    expect(mounted).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(probe).toHaveBeenCalledTimes(2);
  });

  it("keeps the draft through a failed probe and permits explicit rechecking", async () => {
    probe
      .mockResolvedValueOnce(status("failed"))
      .mockResolvedValue(status("ready"));
    render(
      <MemoryRouter>
        <StartupGate>
          <div>authenticated child</div>
        </StartupGate>
      </MemoryRouter>,
    );
    await act(async () => {});
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "保留内容" },
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("authenticated child")).not.toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(probe).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "startup.retry" }));
    await act(async () => {});
    expect(screen.getByText("authenticated child")).toBeInTheDocument();
  });

  it("retries an unreachable shell without entering login or activation", async () => {
    probe.mockRejectedValue(new Error("offline"));
    render(
      <MemoryRouter>
        <StartupGate>
          <div>authenticated child</div>
        </StartupGate>
      </MemoryRouter>,
    );
    await act(async () => {});
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(750);
    });
    expect(probe).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("authenticated child")).not.toBeInTheDocument();
  });

  it("leaves browser and legacy desktop entry behavior unchanged", () => {
    delete document.documentElement.dataset.xfriendDesktop;
    render(
      <MemoryRouter>
        <StartupGate>
          <div>web child</div>
        </StartupGate>
      </MemoryRouter>,
    );
    expect(screen.getByText("web child")).toBeInTheDocument();
    expect(probe).not.toHaveBeenCalled();
  });
});
