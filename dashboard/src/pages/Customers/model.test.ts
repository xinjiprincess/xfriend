import { expect, it } from "vitest";
import { proposedProfile, redactSyncSecret, validDraft } from "./model";
import type { SyncEntity } from "../../api/modules/customerSync";
const customer: SyncEntity = {
  entity_type: "customer",
  entity_id: "c",
  revision: 3,
  deleted: false,
  payload: {
    name: "A",
    extension: { preserved: true },
    insight: { budget: "existing", grade: "confirmed" },
  },
};
it("redacts Android and desktop secrets recursively without hiding xfpack metadata", () => {
  const preview = JSON.parse(
    JSON.stringify(
      {
        apiKey: "android",
        key: "desktop",
        nested: { api_key: "alias", accessToken: "token" },
        _xfpack: { resource: { engine: "dsh" } },
      },
      redactSyncSecret,
    ),
  );
  expect(preview.apiKey).toBe("••••••");
  expect(preview.key).toBe("••••••");
  expect(preview.nested).toEqual({ api_key: "••••••", accessToken: "••••••" });
  expect(preview._xfpack.resource.engine).toBe("dsh");
});
it("AI proposals preserve confirmed and unknown fields without mutating the customer", () => {
  const proposal = proposedProfile(customer, {
    ...customer,
    entity_type: "communication",
    payload: {
      analysisJson: JSON.stringify({
        profile: {
          summary: "candidate",
          budget: "",
          grade: "待判断",
          injected: "ignored",
        },
      }),
    },
  });
  expect(proposal?.payload.insight).toEqual({
    budget: "existing",
    grade: "confirmed",
    summary: "candidate",
  });
  expect(customer.payload.insight).not.toHaveProperty("summary");
  expect(proposal?.payload.extension).toEqual({ preserved: true });
});
it("invalid analysis is not turned into a proposal", () => {
  expect(
    proposedProfile(customer, {
      ...customer,
      payload: { analysisJson: "bad" },
    }),
  ).toBeNull();
});
it("validates probability ranges and required names", () => {
  expect(validDraft(customer)).toBe(true);
  expect(validDraft({ ...customer, payload: { name: " " } })).toBe(false);
  expect(
    validDraft({
      ...customer,
      entity_type: "opportunity",
      payload: { title: "Deal", probabilityLow: "80", probabilityHigh: "20" },
    }),
  ).toBe(false);
});
