import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { voiceApi } from "../../api/modules/voice";
import { useAssistantVoice } from "./useAssistantVoice";

vi.mock("../../api/modules/voice", () => ({
  voiceApi: { getActive: vi.fn(), getPreferences: vi.fn() },
}));

describe("assistant recognition selection", () => {
  beforeEach(() => {
    vi.mocked(voiceApi.getActive).mockResolvedValue({
      stt: "browser",
      tts: "browser",
    });
    vi.mocked(voiceApi.getPreferences).mockResolvedValue({
      stt: "",
      tts: "",
      voice_id: "",
      auto_read: false,
      auto_send: false,
    });
  });

  it("keeps browser recognition on the native path", async () => {
    const { result } = renderHook(() => useAssistantVoice(vi.fn(), vi.fn()));
    await waitFor(() => expect(voiceApi.getPreferences).toHaveBeenCalled());
    expect(result.current.service).toBe(false);
  });

  it("uses the user's ASR instead of the global browser default", async () => {
    vi.mocked(voiceApi.getPreferences).mockResolvedValue({
      stt: "mimo-stt",
      tts: "",
      voice_id: "",
      auto_read: false,
      auto_send: false,
    });
    const { result } = renderHook(() => useAssistantVoice(vi.fn(), vi.fn()));
    await waitFor(() => expect(result.current.service).toBe(true));
  });
});
