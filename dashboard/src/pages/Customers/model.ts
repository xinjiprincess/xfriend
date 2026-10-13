import type { SyncEntity } from "../../api/modules/customerSync";

/** JSON previews must cover both desktop and Android credential aliases. */
export function redactSyncSecret(key: string, value: unknown): unknown {
  return /^(key|apikey|apisecret|secret|secrets|password|token|accesstoken|refreshtoken|authorization|credential|credentials|privatekey)$/.test(
    key.replace(/[_-]/g, "").toLowerCase(),
  )
    ? "••••••"
    : value;
}

export const profileFields = [
  "summary",
  "preferences",
  "needs",
  "budget",
  "timing",
  "decisionRole",
  "grade",
] as const;
export function proposedProfile(
  customer: SyncEntity,
  report: SyncEntity,
): SyncEntity | null {
  try {
    const analysis = JSON.parse(String(report.payload.analysisJson || ""));
    const profile = analysis.profile;
    if (!profile || typeof profile !== "object") return null;
    const prior = customer.payload.insight;
    const insight: Record<string, unknown> =
      prior && typeof prior === "object" ? { ...prior } : {};
    let changed = false;
    for (const key of profileFields) {
      if (
        typeof profile[key] === "string" &&
        profile[key].trim() &&
        !(key === "grade" && profile[key] === "待判断")
      ) {
        insight[key] = profile[key];
        changed = true;
      }
    }
    return changed
      ? { ...customer, payload: { ...customer.payload, insight } }
      : null;
  } catch {
    return null;
  }
}

export function validDraft(entity: SyncEntity): boolean {
  const p = entity.payload;
  if (
    !String(
      p[entity.entity_type === "customer" ? "name" : "title"] || "",
    ).trim()
  )
    return false;
  for (const field of ["dueDate", "expectedDate"]) {
    const value = p[field];
    if (
      value &&
      (!/^\d{4}-\d{2}-\d{2}$/.test(String(value)) ||
        !Number.isFinite(new Date(String(value)).getTime()) ||
        new Date(String(value)).toISOString().slice(0, 10) !== value)
    )
      return false;
  }
  if (p.probabilityLow || p.probabilityHigh) {
    if (
      p.probabilityLow === "" ||
      p.probabilityHigh === "" ||
      p.probabilityLow == null ||
      p.probabilityHigh == null
    )
      return false;
    const low = Number(p.probabilityLow),
      high = Number(p.probabilityHigh);
    if (
      !Number.isFinite(low) ||
      !Number.isFinite(high) ||
      low < 0 ||
      high > 100 ||
      low > high
    )
      return false;
  }
  return true;
}
