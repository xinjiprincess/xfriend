"""Platform credentials, ownership and tenant isolation."""

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from octop.infra.connectors.platform_collaboration import PlatformCollaboration, platform_origin


class Settings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


class Secrets:
    key: bytes | None = None

    def get_or_create(self, key: str, factory: Any) -> bytes:
        if self.key is None:
            self.key = factory()
        return self.key


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/api",
        "https://example.com?token=secret",
    ],
)
def test_origin_rejects_unsafe_locations(origin: str) -> None:
    with pytest.raises(ValueError):
        platform_origin(origin)


@pytest.mark.asyncio
async def test_token_is_encrypted_and_device_ownership_checked() -> None:
    settings = Settings()
    service = PlatformCollaboration(SimpleNamespace(settings_repo=settings, secret_repo=Secrets()))  # type: ignore[arg-type]
    account = await service.account(1)
    await account.client.aclose()
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        data: Any = {}
        if request.url.path.endswith("/config"):
            data = {"openCaptcha": "0", "captchaSceneId": "x", "captchaPrefix": "x"}
        elif request.url.path.endswith("passwordLogin"):
            data = {"token": "private-access-token"}
        elif request.url.path.endswith("getLoginInfo"):
            assert request.headers["authorization"] == "Bearer private-access-token"
            data = {"id": 12, "userName": "Alice"}
        elif request.url.path.endswith("/list"):
            data = [
                {"id": 2, "userId": 12, "name": "my-pc", "configKey": "private-device-key"},
                {"id": 3, "userId": 13, "name": "other-pc"},
            ]
        return httpx.Response(200, json={"code": "0000", "data": data})

    account.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        result = await service.login(1, "https://platform.test", "alice", "password")
        assert result["logged_in"]
        assert "private-access-token" not in str(result)
        assert "private-access-token" not in str(settings.values)
        devices = await service.devices(1)
        assert [row["id"] for row in devices] == [2]
        assert "configKey" not in str(devices)
        assert not (await service.account(2)).token
        assert (await service.logout(1))["logged_in"] is False
        assert "/api/user/passwordLogin" in calls
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_unverified_user_cannot_list_devices() -> None:
    service = PlatformCollaboration(
        SimpleNamespace(settings_repo=Settings(), secret_repo=Secrets())
    )  # type: ignore[arg-type]
    account = await service.account(1)
    await account.client.aclose()
    account.token = "expired"
    account.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": "0000", "data": {}})
        )
    )
    try:
        with pytest.raises(ValueError, match="LOGIN_REQUIRED"):
            await service.devices(1)
    finally:
        await service.shutdown()
