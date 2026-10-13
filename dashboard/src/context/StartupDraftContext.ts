import { createContext, useContext } from "react";

/** Ephemeral text typed in this window before the service is ready. */
export const StartupDraftContext = createContext<{
  ready: boolean;
  startupError?: string | null;
  draft: string;
  setDraft: (text: string) => void;
  destination: string | null;
  setDestination: (path: string | null) => void;
}>({
  ready: true,
  draft: "",
  setDraft: () => {},
  destination: null,
  setDestination: () => {},
});

export function useStartupDraft() {
  return useContext(StartupDraftContext);
}
