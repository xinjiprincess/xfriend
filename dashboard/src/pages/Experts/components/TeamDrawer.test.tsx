import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopAgent } from "../../../context/AgentContext";
import { teamsApi, type TeamRecord } from "../../../api/modules/teams";
import TeamDrawer from "./TeamDrawer";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

vi.mock("../../../api/modules/teams", () => ({
  teamsApi: {
    get: vi.fn(),
    update: vi.fn(),
    create: vi.fn(),
    list: vi.fn(),
    templateFiles: vi.fn(),
  },
}));
vi.mock("../../../hooks/useAgentFormResources", () => ({
  useAgentFormResources: () => ({
    models: [{ provider_name: "p", model: "a" }],
    modelsLoading: false,
  }),
}));

const team = {
  agent_id: "team",
  name: "Test",
  kind: "team",
  member_ids: ["a", "b"],
} as OctopAgent;
const record: TeamRecord = {
  team_id: "team",
  agent_id: "team",
  name: "Test",
  kind: "team",
  member_ids: ["a", "b"],
  members: [
    { agent_id: "a", name: "Alpha" },
    { agent_id: "b", name: "Beta" },
  ],
  system_prompt: "Review carefully",
  member_models: { a: "p/a" },
  default_model: "p/a",
  revision: "revision-1",
};

describe("TeamDrawer configuration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(teamsApi.templateFiles).mockResolvedValue([]);
    vi.mocked(teamsApi.get).mockResolvedValue(record);
    vi.mocked(teamsApi.update).mockResolvedValue(record);
  });

  it("saves the loaded revision, host prompt and team-only member model", async () => {
    const onSaved = vi.fn();
    render(
      <TeamDrawer
        open
        mode="edit"
        team={team}
        experts={[]}
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );
    const prompt = await screen.findByDisplayValue("Review carefully");
    fireEvent.change(prompt, { target: { value: "Check evidence first" } });
    fireEvent.click(screen.getByRole("button", { name: "common.save" }));
    await waitFor(() =>
      expect(teamsApi.update).toHaveBeenCalledWith(
        "team",
        expect.objectContaining({
          system_prompt: "Check evidence first",
          member_models: { a: "p/a" },
          default_model: "p/a",
          member_ids: ["a", "b"],
          revision: "revision-1",
        }),
      ),
    );
    expect(onSaved).toHaveBeenCalledWith(record);
  });

  it("blocks saving a failed load but allows opening a fresh create form", async () => {
    vi.mocked(teamsApi.get).mockRejectedValue(new Error("offline"));
    const props = {
      open: true,
      experts: [],
      onClose: vi.fn(),
      onSaved: vi.fn(),
    };
    const { rerender } = render(
      <TeamDrawer {...props} mode="edit" team={team} />,
    );
    await screen.findByText("experts.teams.loadFailed");
    expect(screen.getByRole("button", { name: "common.save" })).toBeDisabled();
    expect(teamsApi.update).not.toHaveBeenCalled();
    rerender(<TeamDrawer {...props} mode="create" />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "common.create" }),
      ).toBeEnabled(),
    );
    expect(
      screen.queryByText("experts.teams.loadFailed"),
    ).not.toBeInTheDocument();
  });
});
