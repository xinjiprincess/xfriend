import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { request } from "../request";
import { customerSyncApi, type SyncJournal } from "./customerSync";

vi.mock("../request", () => ({ request: vi.fn(), requestBlob: vi.fn() }));
const scope = { user: "u", space: "s" };
const op = {
  operation_id: "stable",
  entity_type: "customer" as const,
  entity_id: "c",
  base_revision: 0,
  deleted: false,
  payload: { name: "Test" },
};
const initial: SyncJournal = {
  cursor: 0,
  entities: [],
  pending: [op],
  conflicts: {},
};
const fetcher = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => vi.unstubAllGlobals());
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });

it("pins account scope, sends stable operations, and pulls from the local cursor", async () => {
  vi.mocked(request)
    .mockResolvedValueOnce(initial)
    .mockResolvedValueOnce({ ...initial, pending: [] })
    .mockResolvedValueOnce({ ...initial, pending: [], cursor: 8 });
  fetcher
    .mockResolvedValueOnce(
      response({
        results: [{ operation_id: "stable", status: "duplicate", revision: 8 }],
        cursor: 99,
      }),
    )
    .mockResolvedValueOnce(
      response({ changes: [], cursor: 8, has_more: false }),
    );
  const result = await customerSyncApi.sync(scope);
  expect(result.cursor).toBe(8);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    space_id: "s",
    device_id: "desktop",
    operations: [op],
  });
  expect(fetcher.mock.calls[0][1].headers).toEqual({
    "Content-Type": "application/json",
    "X-Xfriend-Sync-Scope": "u:s",
  });
  expect(fetcher.mock.calls[1][0]).toContain("cursor=0");
  expect(fetcher.mock.calls[0][1].headers).not.toHaveProperty("Authorization");
});

it("never acknowledges a failed cloud response and retries the same operation", async () => {
  vi.mocked(request).mockResolvedValue(initial);
  fetcher.mockImplementation(async () =>
    response({ code: "SYNC_OFFLINE" }, 502),
  );
  await expect(customerSyncApi.sync(scope)).rejects.toThrow("SYNC_OFFLINE");
  await expect(customerSyncApi.sync(scope)).rejects.toThrow("SYNC_OFFLINE");
  expect(
    vi
      .mocked(request)
      .mock.calls.every(([path]) => !path.includes("acknowledge")),
  ).toBe(true);
  expect(
    JSON.parse(fetcher.mock.calls[1][1].body).operations[0].operation_id,
  ).toBe("stable");
});

it("rejects missing acknowledgements and nonadvancing pages", async () => {
  vi.mocked(request).mockResolvedValue(initial);
  fetcher.mockResolvedValueOnce(response({ results: [], cursor: 8 }));
  await expect(customerSyncApi.sync(scope)).rejects.toThrow("SYNC_RESPONSE");
  vi.mocked(request).mockResolvedValue({ ...initial, pending: [] });
  fetcher.mockResolvedValueOnce(
    response({ changes: [], cursor: 0, has_more: true }),
  );
  await expect(customerSyncApi.sync(scope)).rejects.toThrow("SYNC_RESPONSE");
});

it("does not resend conflicts and continues paginated pulls", async () => {
  const state = {
    ...initial,
    conflicts: {
      stable: {
        entity_type: "customer",
        entity_id: "c",
        revision: 1,
        deleted: false,
        payload: {},
      },
    },
  };
  vi.mocked(request)
    .mockResolvedValueOnce(state)
    .mockResolvedValueOnce({ ...state, cursor: 10 })
    .mockResolvedValueOnce({ ...state, cursor: 11 });
  fetcher
    .mockResolvedValueOnce(
      response({ changes: [], cursor: 10, has_more: true }),
    )
    .mockResolvedValueOnce(
      response({ changes: [], cursor: 11, has_more: false }),
    );
  await customerSyncApi.sync(scope);
  expect(fetcher.mock.calls[0][0]).toContain("pull?");
  expect(fetcher.mock.calls[1][0]).toContain("cursor=10");
});
