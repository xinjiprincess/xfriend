import { afterEach, describe, expect, it, vi } from "vitest";
import { readDesktopStartup } from "./desktopStartup";

afterEach(() => vi.unstubAllGlobals());

describe("desktop readiness protocol", () => {
  it.each([
    {
      application: "another-service",
      protocol: 1,
      state: "ready",
      phase: "ready",
      elapsed_ms: 1,
    },
    {
      application: "xfriend",
      protocol: 2,
      state: "ready",
      phase: "ready",
      elapsed_ms: 1,
    },
    { application: "xfriend", protocol: 1, state: "ready", elapsed_ms: 1 },
  ])("rejects unrelated or incompatible services: %j", async (body) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(body))),
    );
    await expect(
      readDesktopStartup(new AbortController().signal),
    ).rejects.toThrow("Unexpected desktop startup protocol");
  });

  it("does not treat an HTTP error as readiness", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("starting", { status: 503 })),
    );
    await expect(
      readDesktopStartup(new AbortController().signal),
    ).rejects.toThrow("Desktop startup probe failed");
  });
});
