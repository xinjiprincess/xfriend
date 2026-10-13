export interface PhoneConnection {
  enabled: boolean;
  mode?: "usb" | "https";
  address?: string;
  code?: string;
  connections?: number;
  port?: number;
}
export interface PhoneConnectionOptions {
  mode: "usb" | "https";
  port: number;
  origin?: string;
  certificate?: string;
  key?: string;
}
async function call<T>(action: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/desktop/mobile/${action}`, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.message || `HTTP ${response.status}`);
  return value as T;
}
export const phoneConnectionApi = {
  status: () => call<PhoneConnection>("status"),
  start: (options: PhoneConnectionOptions) =>
    call<PhoneConnection>("start", options),
  stop: () => call<PhoneConnection>("stop", {}),
  usb: () => call<{ ok: boolean }>("usb", {}),
};
