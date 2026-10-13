import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CustomerCenter } from "./index";
import {
  customerSyncApi,
  type SyncJournal,
} from "../../api/modules/customerSync";
import type { ReactNode } from "react";

vi.mock("./strings", () => ({}));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
vi.mock("../../layouts/PageShell", () => ({
  default: ({
    children,
    actions,
  }: {
    children: ReactNode;
    actions: ReactNode;
  }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "Asia/Shanghai",
}));
vi.mock("../../api/modules/transfer", () => ({
  transferApi: { inventory: vi.fn() },
}));
vi.mock("../../api/modules/customerSync", () => ({
  customerSyncApi: {
    snapshot: vi.fn(),
    sync: vi.fn(),
    edit: vi.fn(),
    resolve: vi.fn(),
  },
}));
const scope = { user: "u", space: "s" };
const state: SyncJournal = {
  cursor: 2,
  pending: [],
  conflicts: {},
  entities: [
    {
      entity_type: "customer",
      entity_id: "a",
      revision: 1,
      deleted: false,
      payload: { name: "Alice", extension: "keep", insight: { budget: "old" } },
    },
    {
      entity_type: "customer",
      entity_id: "b",
      revision: 2,
      deleted: false,
      payload: { name: "Bob" },
    },
    {
      entity_type: "communication",
      entity_id: "r",
      revision: 2,
      deleted: false,
      payload: {
        title: "Alice report",
        customerId: "a",
        report: "Original evidence",
        created: 1767225600000,
        analysisJson: JSON.stringify({
          profile: { summary: "Proposed summary" },
        }),
      },
    },
    {
      entity_type: "communication",
      entity_id: "r2",
      revision: 2,
      deleted: false,
      payload: { title: "Bob private report", customerId: "b" },
    },
  ],
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(customerSyncApi.snapshot).mockResolvedValue(state);
  vi.mocked(customerSyncApi.edit).mockResolvedValue(state);
});
afterEach(cleanup);
it("confirms that deleting a customer retains and unlinks their records", async () => {
  render(<CustomerCenter scope={scope} />);
  fireEvent.click(await screen.findByRole("button", { name: /Alice/ }));
  fireEvent.click(screen.getAllByRole("button", { name: "delete" })[0]);
  const confirmation = await screen.findByRole("tooltip");
  expect(
    within(confirmation).getByText("deleteCustomerDetails"),
  ).toBeInTheDocument();
  expect(customerSyncApi.edit).not.toHaveBeenCalled();
  fireEvent.click(within(confirmation).getByRole("button", { name: "cancel" }));
  expect(customerSyncApi.edit).not.toHaveBeenCalled();
  fireEvent.click(screen.getAllByRole("button", { name: "delete" })[0]);
  fireEvent.click(
    within(await screen.findByRole("tooltip")).getByRole("button", {
      name: "delete",
    }),
  );
  await waitFor(() =>
    expect(customerSyncApi.edit).toHaveBeenCalledWith(
      scope,
      expect.objectContaining({
        entity_type: "customer",
        entity_id: "a",
        deleted: true,
      }),
    ),
  );
}, 15000);
it("asks to sync pending children before deleting a customer", async () => {
  vi.mocked(customerSyncApi.snapshot).mockResolvedValue({
    ...state,
    pending: [
      {
        entity_type: "communication",
        entity_id: "r",
        deleted: false,
        payload: state.entities[2].payload,
        base_revision: 2,
        operation_id: "immutable",
      },
    ],
  });
  render(<CustomerCenter scope={scope} />);
  fireEvent.click(await screen.findByRole("button", { name: /Alice/ }));
  fireEvent.click(screen.getAllByRole("button", { name: "delete" })[0]);
  fireEvent.click(
    within(await screen.findByRole("tooltip")).getByRole("button", {
      name: "delete",
    }),
  );
  expect(await screen.findByText("deleteCustomerPending")).toBeInTheDocument();
  expect(customerSyncApi.edit).not.toHaveBeenCalled();
}, 15000);
it("shows only the selected customer's reports and requires confirmation for AI edits", async () => {
  render(<CustomerCenter scope={scope} />);
  fireEvent.click(await screen.findByRole("button", { name: /Alice/ }));
  expect(await screen.findByText("Alice report")).toBeInTheDocument();
  expect(screen.getByText(/2026/)).toBeInTheDocument();
  expect(screen.queryByText("Bob private report")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("reviewAI"));
  const save = await screen.findByRole("button", { name: "save" });
  expect(save).toBeDisabled();
  expect(customerSyncApi.edit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("confirmAI"));
  fireEvent.click(save);
  await waitFor(() =>
    expect(customerSyncApi.edit).toHaveBeenCalledWith(
      scope,
      expect.objectContaining({
        payload: expect.objectContaining({
          extension: "keep",
          insight: { budget: "old", summary: "Proposed summary" },
        }),
      }),
      "ai",
      true,
    ),
  );
}, 15000);
it("displays cloud failure without a success notice", async () => {
  vi.mocked(customerSyncApi.sync).mockRejectedValue(new Error("SYNC_OFFLINE"));
  render(<CustomerCenter scope={scope} />);
  await screen.findByRole("button", { name: /Alice/ });
  fireEvent.click(screen.getByRole("button", { name: "sync" }));
  expect(await screen.findByText(/SYNC_OFFLINE/)).toBeInTheDocument();
  expect(screen.queryByText("success")).not.toBeInTheDocument();
});
