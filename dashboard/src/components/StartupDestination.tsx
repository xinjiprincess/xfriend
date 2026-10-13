import { useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useStartupDraft } from "../context/StartupDraftContext";
import OctopSpinner from "./OctopSpinner";

/** Consume a startup selection only after setup and authentication succeed. */
export default function StartupDestination({
  children,
}: {
  children: ReactNode;
}) {
  const { destination, setDestination } = useStartupDraft();
  const navigate = useNavigate();
  useEffect(() => {
    if (!destination) return;
    navigate(destination, { replace: true });
    setDestination(null);
  }, [destination, navigate, setDestination]);
  return destination ? <OctopSpinner /> : children;
}
