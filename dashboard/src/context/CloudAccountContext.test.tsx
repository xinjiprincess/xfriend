import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CloudAccountProvider } from "./CloudAccountContext";
import { cloudAccountApi } from "../api/modules/cloudAccount";

vi.mock("../api/modules/cloudAccount", () => ({
  usesCloudAccount: () => true,
  cloudAccountApi: { status: vi.fn() },
}));

function view() {
  return render(
    <MemoryRouter initialEntries={["/chat"]}>
      <CloudAccountProvider>
        <Routes>
          <Route path="/chat" element={<div>private-history</div>} />
          <Route path="/register" element={<div>registration-form</div>} />
          <Route path="/login" element={<div>login-form</div>} />
        </Routes>
      </CloudAccountProvider>
    </MemoryRouter>,
  );
}

describe("cloud account entry", () => {
  beforeEach(() => vi.mocked(cloudAccountApi.status).mockReset());
  it("opens login on first use without displaying private history", async () => {
    vi.mocked(cloudAccountApi.status).mockResolvedValue({
      enabled: true,
      state: "first_register",
      remember_account: true,
      keep_login: true,
      identifier: "",
      spaces: [],
    });
    view();
    expect(screen.queryByText("private-history")).not.toBeInTheDocument();
    expect(await screen.findByText("login-form")).toBeInTheDocument();
  });
  it("restores an authenticated identity without rendering login", async () => {
    vi.mocked(cloudAccountApi.status).mockResolvedValue({
      enabled: true,
      state: "authenticated",
      remember_account: true,
      keep_login: true,
      identifier: "13812345678",
      user: { user_id: "user", name: "name", identifier: "13812345678" },
      space_id: "personal",
      spaces: [],
    });
    view();
    expect(await screen.findByText("private-history")).toBeInTheDocument();
    expect(screen.queryByText("login-form")).not.toBeInTheDocument();
  });
});
