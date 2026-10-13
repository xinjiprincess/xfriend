import { request, requestBlob } from "../request";

export type EntityType =
  | "customer"
  | "communication"
  | "opportunity"
  | "followup"
  | "expert"
  | "team"
  | "skill"
  | "knowledge"
  | "model_profile";
export interface SyncEntity {
  entity_type: EntityType;
  entity_id: string;
  revision: number;
  deleted: boolean;
  payload: Record<string, unknown>;
}
export interface SyncOperation extends Omit<SyncEntity, "revision"> {
  operation_id: string;
  base_revision: number;
}
export interface SyncJournal {
  cursor: number;
  entities: SyncEntity[];
  pending: SyncOperation[];
  conflicts: Record<string, SyncEntity>;
}
export interface SyncScope {
  user: string;
  space: string;
}
export interface PushResponse {
  results: {
    operation_id: string;
    status: "applied" | "duplicate" | "conflict";
    revision: number;
    server_entity?: SyncEntity;
  }[];
  cursor: number;
}
export interface PullResponse {
  changes: SyncEntity[];
  cursor: number;
  has_more: boolean;
}
export interface CatalogItem {
  id: string;
  name: string;
  description: string;
  version: string;
  kind: string;
  content: string;
  files?: Record<string, unknown>;
  dependencies?: unknown[];
}

async function local<T>(
  scope: SyncScope,
  path = "",
  body?: unknown,
): Promise<T> {
  return request<T>(
    `/customer-sync${path}?space_id=${encodeURIComponent(scope.space)}`,
    {
      headers: { "X-Xfriend-Sync-Scope": `${scope.user}:${scope.space}` },
      ...(body === undefined
        ? {}
        : { method: "POST", body: JSON.stringify(body) }),
    },
  );
}

async function cloud<T>(
  scope: SyncScope,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api/desktop/sync/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Xfriend-Sync-Scope": `${scope.user}:${scope.space}`,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    signal: AbortSignal.timeout(25000),
  });
  let value: Record<string, unknown>;
  try {
    value = await response.json();
  } catch {
    throw new Error("SYNC_UNAVAILABLE");
  }
  if (!response.ok)
    throw new Error(
      typeof value.code === "string" ? value.code : "SYNC_FAILED",
    );
  return value as T;
}

// Share a flight between mounts: two tabs/clients still safely retry immutable IDs.
const flights = new Map<string, Promise<SyncJournal>>();
async function exchange(scope: SyncScope): Promise<SyncJournal> {
  let state = await customerSyncApi.snapshot(scope);
  const queued = state.pending.filter(
    (op) => !state.conflicts[op.operation_id],
  );
  for (let offset = 0; offset < queued.length; ) {
    const operations: SyncOperation[] = [];
    let bytes = 1024;
    while (offset < queued.length && operations.length < 100) {
      const next = queued[offset];
      const size = new TextEncoder().encode(JSON.stringify(next)).length + 1;
      if (bytes + size > 4 * 1024 * 1024) {
        if (!operations.length) throw new Error("SYNC_ENTITY_TOO_LARGE");
        break;
      }
      bytes += size;
      operations.push(next);
      offset++;
    }
    const response = await cloud<PushResponse>(scope, "push", {
      space_id: scope.space,
      device_id: "desktop",
      operations,
    });
    if (
      !Array.isArray(response.results) ||
      response.results.length !== operations.length ||
      new Set(response.results.map((r) => r.operation_id)).size !==
        operations.length ||
      response.results.some(
        (r) => !operations.some((op) => op.operation_id === r.operation_id),
      )
    )
      throw new Error("SYNC_RESPONSE");
    state = await local(scope, "/acknowledge", response);
  }
  let limit = 100;
  for (let page = 0; page < 1000; page++) {
    let response: PullResponse;
    try {
      response = await cloud<PullResponse>(
        scope,
        `pull?space_id=${encodeURIComponent(scope.space)}&cursor=${
          state.cursor
        }&limit=${limit}`,
      );
    } catch (error) {
      if (
        limit > 1 &&
        error instanceof Error &&
        error.message === "SYNC_RESPONSE"
      ) {
        limit = 1;
        continue;
      }
      throw error;
    }
    if (
      !Array.isArray(response.changes) ||
      typeof response.has_more !== "boolean" ||
      !Number.isSafeInteger(response.cursor) ||
      response.cursor < state.cursor ||
      (response.has_more && response.cursor <= state.cursor)
    )
      throw new Error("SYNC_RESPONSE");
    state = await local(scope, "/pull", response);
    if (!response.has_more) return state;
  }
  throw new Error("SYNC_MORE_PAGES");
}

export const customerSyncApi = {
  snapshot: (scope: SyncScope) => local<SyncJournal>(scope),
  edit: (
    scope: SyncScope,
    entity: SyncEntity,
    source: "manual" | "ai" = "manual",
    confirmed = false,
    sync_secret = false,
  ) =>
    local<SyncJournal>(scope, "/entities", {
      ...entity,
      source,
      confirmed,
      sync_secret,
    }),
  resolve: (
    scope: SyncScope,
    operation_id: string,
    choice: "local" | "server",
  ) => local<SyncJournal>(scope, "/resolve", { operation_id, choice }),
  sync: (scope: SyncScope) => {
    const key = JSON.stringify(scope);
    const current = flights.get(key);
    if (current) return current;
    const next = exchange(scope).finally(() => flights.delete(key));
    flights.set(key, next);
    return next;
  },
  configuration: (
    scope: SyncScope,
    agent_ids: string[],
    provider_ids: number[],
    include_secrets: boolean,
  ) =>
    local<SyncJournal>(scope, "/configuration", {
      agent_ids,
      provider_ids,
      include_skills: true,
      include_secrets,
    }),
  package: (scope: SyncScope) =>
    requestBlob(
      `/customer-sync/configuration/package?space_id=${encodeURIComponent(
        scope.space,
      )}`,
      { headers: { "X-Xfriend-Sync-Scope": `${scope.user}:${scope.space}` } },
    ),
  catalog: (scope: SyncScope, kind: "experts" | "skills", q: string) =>
    cloud<{ items: CatalogItem[] }>(
      scope,
      `catalog/${kind}?q=${encodeURIComponent(q)}&scene=sales`,
    ),
};
