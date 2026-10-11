import { request, requestBlob, requestStream, requestUpload } from "../request";

export interface VoicePreset {
  id: string;
  name: string;
  kind: string;
  capability: "stt" | "tts" | "both";
  free: boolean;
  limited_free?: boolean;
  requires_key: boolean;
  description: string;
}

export interface VoiceProviderRow {
  id: number;
  name: string;
  kind: string;
  capability: "stt" | "tts" | "both";
  base_url: string | null;
  api_key: string | null;
  extra: Record<string, unknown>;
  note: string | null;
  enabled: boolean;
}

export interface ActiveVoice {
  stt: string;
  tts: string;
}

export interface AssistantVoicePreferences {
  stt: string;
  tts: string;
  voice_id: string;
  auto_read: boolean;
  auto_send: boolean;
}

export interface VoiceProviderInput {
  name: string;
  kind: string;
  capability: string;
  base_url?: string | null;
  api_key?: string | null;
  extra_json?: string | null;
  note?: string | null;
}

export interface LibraryVoice {
  id: string;
  name: string;
  description: string;
  mime: string;
  created_at: number;
  mode: string;
}

function recordingFilename(type: string): string {
  const lower = type.toLowerCase();
  if (lower.includes("mp4")) return "recording.m4a";
  if (lower.includes("ogg")) return "recording.ogg";
  if (lower.includes("wav")) return "recording.wav";
  return "recording.webm";
}

export const voiceApi = {
  listLibrary: () => request<LibraryVoice[]>("/voice/library"),
  uploadVoice: (name: string, audio: File) => {
    const form = new FormData();
    form.append("name", name);
    form.append("audio", audio);
    return requestUpload<LibraryVoice>("/voice/library", form);
  },
  designVoice: (body: {
    name: string;
    provider: string;
    description: string;
    text: string;
  }) =>
    request<LibraryVoice>("/voice/library/design", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  renameVoice: (id: string, name: string) =>
    request<LibraryVoice>(`/voice/library/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deleteVoice: (id: string) =>
    request<void>(`/voice/library/${id}`, { method: "DELETE" }),
  voiceSample: (id: string) => requestBlob(`/voice/library/${id}/sample`),
  getPreferences: () =>
    request<AssistantVoicePreferences>("/voice/preferences"),
  setPreferences: (body: AssistantVoicePreferences) =>
    request<AssistantVoicePreferences>("/voice/preferences", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getPresets: () => request<VoicePreset[]>("/voice/presets"),
  getProviders: () => request<VoiceProviderRow[]>("/voice/providers"),
  adminListProviders: () =>
    request<VoiceProviderRow[]>("/admin/voice/providers"),
  getActive: () => request<ActiveVoice>("/voice/active"),
  setActive: (body: { stt?: string; tts?: string }) =>
    request<ActiveVoice>("/voice/active", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  transcribe: (audio: Blob, language = "zh-CN", provider?: string) => {
    const form = new FormData();
    form.append("audio", audio, recordingFilename(audio.type));
    form.append("language", language);
    if (provider) form.append("provider", provider);
    return requestUpload<{ text: string; confidence?: number | null }>(
      "/voice/stt",
      form,
    );
  },
  synthesize: (text: string, provider?: string, voiceId?: string) =>
    requestBlob("/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        ...(voiceId ? { voice_id: voiceId } : {}),
        ...(provider ? { provider } : {}),
      }),
    }),
  /**
   * Streamed variant for low-latency playback. The server flushes chunks as
   * they arrive (MiMo streams live WAV); other providers stream MP3, in
   * which case the buffered `synthesize` path is used instead.
   */
  synthesizeStream: (
    text: string,
    provider?: string,
    voiceId?: string,
  ): Promise<{ contentType: string; body: ReadableStream<Uint8Array> }> =>
    requestStream("/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        ...(voiceId ? { voice_id: voiceId } : {}),
        ...(provider ? { provider } : {}),
      }),
    }),
  createProvider: (body: VoiceProviderInput) =>
    request<VoiceProviderRow>("/admin/voice/providers", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchProvider: (
    id: number,
    body: Partial<{
      kind: string;
      capability: string;
      base_url: string | null;
      api_key: string | null;
      extra_json: string | null;
      note: string | null;
      enabled: boolean;
    }>,
  ) =>
    request<VoiceProviderRow>(`/admin/voice/providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteProvider: (id: number) =>
    request<void>(`/admin/voice/providers/${id}`, { method: "DELETE" }),
  testProvider: (id: number, mode: "stt" | "tts") =>
    request<{ ok: boolean; error?: string }>(
      `/admin/voice/providers/${id}/test`,
      {
        method: "POST",
        body: JSON.stringify({ mode }),
      },
    ),
  testConfiguration: (body: VoiceProviderInput & { mode: "stt" | "tts" }) =>
    request<{ ok: boolean; error?: string }>(
      "/admin/voice/providers/test-configuration",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
};
