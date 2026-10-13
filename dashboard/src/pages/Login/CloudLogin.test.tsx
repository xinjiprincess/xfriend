import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import CloudLogin from "./CloudLogin";
import { cloudAccountApi } from "../../api/modules/cloudAccount";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../context/CloudAccountContext", () => ({
  useCloudAccount: () => ({
    status: { remember_account: true, keep_login: true, identifier: "" },
    update: vi.fn(),
  }),
}));
vi.mock("../../api/modules/cloudAccount", () => ({
  cloudAccountApi: {
    register: vi.fn().mockResolvedValue({ state: "authenticated" }),
  },
}));

describe("cloud registration without Python", () => {
  it("accepts phone and password input before the local service is available", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/register"]}>
        <CloudLogin />
      </MemoryRouter>,
    );
    const phone = screen.getByRole("textbox", { name: "cloudAccount.phone" });
    const password = screen.getByLabelText("cloudAccount.password", {
      exact: true,
    });
    await user.type(phone, "13812345678");
    await user.type(password, "test-long-password");
    expect(phone).toHaveValue("13812345678");
    expect(password).toHaveValue("test-long-password");
    expect(
      screen.getByRole("button", { name: "cloudAccount.registerSubmit" }),
    ).toBeEnabled();
    expect(screen.queryByText("cloudAccount.sendCode")).not.toBeInTheDocument();
    expect(screen.queryByText("cloudAccount.code")).not.toBeInTheDocument();
  });

  it("submits a nickname and password without a verification challenge", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <CloudLogin />
      </MemoryRouter>,
    );
    expect(
      screen.queryByLabelText("cloudAccount.name"),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "cloudAccount.newAccount" }),
    );
    const nickname = screen.getByRole("textbox", { name: "cloudAccount.name" });
    expect(nickname).toHaveAttribute("autocomplete", "nickname");
    await user.type(nickname, "friend");
    await user.type(
      screen.getByRole("textbox", { name: "cloudAccount.email" }),
      "friend@example.com",
    );
    await user.type(
      screen.getByRole("textbox", { name: "cloudAccount.phone" }),
      "13812345678",
    );
    await user.type(
      screen.getByLabelText("cloudAccount.password", { exact: true }),
      "x",
    );
    await user.type(screen.getByLabelText("cloudAccount.confirm"), "x");
    await user.click(
      screen.getByRole("button", { name: "cloudAccount.registerSubmit" }),
    );
    await waitFor(() =>
      expect(cloudAccountApi.register).toHaveBeenCalledWith({
        name: "friend",
        email: "friend@example.com",
        phone: "13812345678",
        password: "x",
        remember_account: true,
        keep_login: true,
      }),
    );
  });
});
