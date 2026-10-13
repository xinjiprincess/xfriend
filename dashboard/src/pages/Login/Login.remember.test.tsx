import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { StartupDraftContext } from "../../context/StartupDraftContext";

vi.mock("../../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));

vi.mock("../../api/modules/auth", () => ({
  authApi: {
    getAuthStatus: () => Promise.resolve({ setup_required: false }),
    getOauthStatus: () => Promise.resolve({ providers: [] }),
    getCaptcha: () => Promise.resolve({ provider: "none" }),
  },
}));

vi.mock("../../utils/locale", () => ({
  applyGuestLocale: () => Promise.resolve(),
  applyUserLocale: () => Promise.resolve(),
}));

vi.mock("./CaptchaField", () => ({
  default: () => null,
}));

import LoginPage from "./index";

describe("Login remember-me checkbox", () => {
  it.each([null, "本机服务启动失败，请重新启动软件"])(
    "keeps login visible while unavailable: %s",
    (startupError) => {
      render(
        <MemoryRouter>
          <StartupDraftContext.Provider
            value={{
              ready: false,
              startupError,
              draft: "",
              setDraft: () => {},
              destination: null,
              setDestination: () => {},
            }}
          >
            <LoginPage />
          </StartupDraftContext.Provider>
        </MemoryRouter>,
      );
      expect(
        screen.getByRole(startupError ? "alert" : "status"),
      ).toHaveTextContent(
        startupError || /startup\.preparing|正在准备本机服务/,
      );
      expect(
        screen.getByRole("button", { name: /login\.submit|登录|Sign in/ }),
      ).toBeDisabled();
    },
  );

  it("renders checked by default", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    const checkbox = screen.getByRole("checkbox", {
      name: /login\.remember|记住登录状态|Remember me/,
    });
    expect(checkbox).toBeChecked();
    expect(
      screen.getByText(/login\.usernameHint|用户名或邮箱|username or email/i),
    ).toBeInTheDocument();
  });
});
