import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import PhoneConnection from "./PhoneConnection";
import { phoneConnectionApi } from "../../api/modules/phoneConnection";

vi.mock("../../api/modules/phoneConnection", () => ({
  phoneConnectionApi: {
    status: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    usb: vi.fn(),
  },
}));
vi.mock("../../api/modules/cloudAccount", () => ({
  usesCloudAccount: () => true,
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
beforeEach(() => {
  vi.clearAllMocks();
  document.documentElement.dataset.xfriendDesktop = "1";
  vi.mocked(phoneConnectionApi.status).mockResolvedValue({ enabled: false });
});
it("starts USB pairing and revokes it from the desktop", async () => {
  vi.mocked(phoneConnectionApi.start).mockResolvedValue({
    enabled: true,
    mode: "usb",
    address: "http://127.0.0.1:8765",
    code: "pair-test",
    port: 8765,
    connections: 0,
  });
  vi.mocked(phoneConnectionApi.stop).mockResolvedValue({ enabled: false });
  render(<PhoneConnection />);
  await waitFor(() => expect(phoneConnectionApi.status).toHaveBeenCalled());
  fireEvent.click(screen.getByText("phoneConnection.start"));
  await screen.findByText("pair-test");
  expect(phoneConnectionApi.start).toHaveBeenCalledWith({
    mode: "usb",
    port: 8765,
  });
  fireEvent.click(screen.getByText("phoneConnection.stop"));
  await waitFor(() =>
    expect(screen.queryByText("pair-test")).not.toBeInTheDocument(),
  );
  expect(phoneConnectionApi.stop).toHaveBeenCalledOnce();
});
it("shows actionable USB errors and keeps the connection available", async () => {
  vi.mocked(phoneConnectionApi.status).mockResolvedValue({
    enabled: true,
    mode: "usb",
    port: 8765,
    code: "pair-test",
  });
  vi.mocked(phoneConnectionApi.usb).mockRejectedValue(
    new Error("ADB unavailable"),
  );
  render(<PhoneConnection />);
  fireEvent.click(await screen.findByText("phoneConnection.usbSetup"));
  await screen.findByText("ADB unavailable");
  expect(screen.getByText("pair-test")).toBeInTheDocument();
});
