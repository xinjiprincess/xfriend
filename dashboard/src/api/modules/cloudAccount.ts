export interface CloudAccountStatus {
  enabled: boolean;
  state:
    | "first_register"
    | "logged_out"
    | "restoring"
    | "authenticated"
    | "reauth_required";
  remember_account: boolean;
  keep_login: boolean;
  identifier: string;
  user?: { user_id: string; name: string; identifier: string };
  spaces: {
    space_id: string;
    name: string;
    kind: "personal" | "enterprise";
    role: string;
  }[];
  space_id?: string;
  offline_until?: number;
  message?: string;
}

export function usesCloudAccount(): boolean {
  return document.documentElement.dataset.xfriendCloud === "1";
}

async function call<T>(action: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/desktop/auth/${action}`, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    signal: AbortSignal.timeout(12000),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.message || "账号服务暂时不可用");
  return value as T;
}

export const cloudAccountApi = {
  status: () => call<CloudAccountStatus>("status"),
  login: (body: Record<string, unknown>) =>
    call<CloudAccountStatus>("login", body),
  register: (body: Record<string, unknown>) =>
    call<CloudAccountStatus>("register", body),
  refresh: () => call<CloudAccountStatus>("refresh", {}),
  switchSpace: (space_id: string) =>
    call<CloudAccountStatus>("switch-space", { space_id }),
  logout: () => call<CloudAccountStatus>("logout", {}),
  forget: () => call<CloudAccountStatus>("forget", {}),
  challenge: (phone: string) =>
    call<{ challenge_id: string; expires_in: number }>("challenges", { phone }),
  reset: (body: Record<string, unknown>) => call<null>("password-reset", body),
};
