import { useCallback, useEffect, useRef, useState } from "react";
import { voiceApi } from "../../api/modules/voice";
import { recordedBlobToWav } from "../../utils/recordedBlobToWav";

/** Audio is recorded only after a wake event; keyword listening stays local. */
export function useAssistantVoice(
  onText: (text: string) => Promise<void>,
  onError: (error: string) => void,
) {
  const [service, setService] = useState(false);
  const [busy, setBusy] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const mounted = useRef(true);
  const cancelled = useRef(false);
  const providerRef = useRef<string>();
  useEffect(() => {
    mounted.current = true;
    void Promise.all([voiceApi.getActive(), voiceApi.getPreferences()])
      .then(([active, preferences]) => {
        const provider = preferences.stt || active.stt;
        providerRef.current = preferences.stt || undefined;
        if (mounted.current)
          setService(Boolean(provider && provider !== "browser"));
      })
      .catch(() => {});
    return () => {
      mounted.current = false;
      cancelled.current = true;
      stopRef.current?.();
    };
  }, []);

  const record = useCallback(async () => {
    if (!service || stopRef.current) return;
    setBusy(true);
    cancelled.current = false;
    let stream: MediaStream | null = null;
    let context: AudioContext | null = null;
    let timer: number | undefined;
    let limit: number | undefined;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          ...(localStorage.getItem("xfriend_microphone")
            ? {
                deviceId: {
                  exact: localStorage.getItem("xfriend_microphone")!,
                },
              }
            : {}),
        },
      });
      if (!mounted.current || cancelled.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const recorder = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      context = new AudioContext();
      const analyser = context.createAnalyser();
      context.createMediaStreamSource(stream).connect(analyser);
      void context.resume();
      const samples = new Float32Array(analyser.fftSize);
      let heardSpeech = false;
      let lastSpeech = performance.now();
      const audio = await new Promise<Blob>((resolve, reject) => {
        stopRef.current = () => {
          if (recorder.state !== "inactive") recorder.stop();
        };
        recorder.ondataavailable = (event) => {
          if (event.data.size) chunks.push(event.data);
        };
        recorder.onerror = () => reject(new Error("recording-failed"));
        recorder.onstop = () =>
          resolve(new Blob(chunks, { type: recorder.mimeType }));
        recorder.start();
        timer = window.setInterval(() => {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(
            samples.reduce((sum, value) => sum + value * value, 0) /
              samples.length,
          );
          if (rms > 0.015) {
            heardSpeech = true;
            lastSpeech = performance.now();
          }
          if (heardSpeech && performance.now() - lastSpeech > 1200)
            stopRef.current?.();
        }, 100);
        limit = window.setTimeout(() => stopRef.current?.(), 10000);
      });
      stream.getTracks().forEach((track) => track.stop());
      if (!mounted.current || cancelled.current) return;
      if (!audio.size) throw new Error("no-speech");
      const result = await voiceApi.transcribe(
        await recordedBlobToWav(audio),
        "zh-CN",
        providerRef.current,
      );
      if (!mounted.current || cancelled.current) return;
      if (!result.text.trim()) throw new Error("no-speech");
      await onText(result.text);
    } catch (error) {
      if (mounted.current) onError(String(error));
    } finally {
      window.clearInterval(timer);
      window.clearTimeout(limit);
      stream?.getTracks().forEach((track) => track.stop());
      if (context) void context.close();
      stopRef.current = null;
      if (mounted.current) setBusy(false);
    }
  }, [service, onText, onError]);
  return {
    service,
    busy,
    record,
    stop: () => {
      cancelled.current = true;
      stopRef.current?.();
    },
  };
}
