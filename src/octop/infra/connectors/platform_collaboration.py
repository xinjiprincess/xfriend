"""PCMATE-compatible platform account and device discovery, independent of its UI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from octop.infra.connectors.crypto import decrypt_credentials, encrypt_credentials
from octop.infra.db.services import SharedServices


def platform_origin(raw: str) -> str:
    parsed = urlsplit(raw.strip() if "://" in raw else "https://" + raw.strip())
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("INVALID_ADDRESS")
    if not parsed.hostname or parsed.scheme not in {"https", "http"}:
        raise ValueError("INVALID_ADDRESS")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("HTTPS_REQUIRED")
    # Accessing port also validates malformed and out-of-range port numbers.
    _ = parsed.port
    return f"{parsed.scheme}://{parsed.netloc}"


@dataclass
class Account:
    origin: str = "https://www.ciyuancenter.cc"
    token: str = ""
    user: dict[str, Any] | None = None
    tenants: list[dict[str, Any]] = field(default_factory=list)
    client: httpx.AsyncClient = field(
        default_factory=lambda: httpx.AsyncClient(timeout=20, follow_redirects=False)
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def view(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "logged_in": self.user is not None,
            "user": self.user,
            "tenants": self.tenants,
        }


class PlatformCollaboration:
    def __init__(self, services: SharedServices) -> None:
        self.services = services
        self.accounts: dict[int, Account] = {}

    async def _persist(self, user_id: int, account: Account) -> None:
        def save() -> None:
            blob = encrypt_credentials(
                self.services.secret_repo, {"origin": account.origin, "token": account.token}
            )
            self.services.settings_repo.set(
                f"platform-collaboration:{user_id}", blob.decode("ascii")
            )

        await asyncio.get_running_loop().run_in_executor(None, save)

    async def account(self, user_id: int) -> Account:
        if user_id not in self.accounts:

            def load() -> dict[str, Any]:
                raw = self.services.settings_repo.get(f"platform-collaboration:{user_id}")
                return (
                    decrypt_credentials(self.services.secret_repo, raw.encode("ascii"))
                    if raw
                    else {}
                )

            stored = await asyncio.get_running_loop().run_in_executor(None, load)
            loaded = Account(
                origin=platform_origin(stored.get("origin", "https://www.ciyuancenter.cc")),
                token=str(stored.get("token") or ""),
            )
            if user_id in self.accounts:
                await loaded.client.aclose()
            else:
                self.accounts[user_id] = loaded
        return self.accounts[user_id]

    async def _request(self, account: Account, path: str, body: Any = None) -> Any:
        response = await account.client.request(
            "GET" if body is None else "POST",
            account.origin + path,
            headers={"Authorization": f"Bearer {account.token}"} if account.token else {},
            **({"json": body} if body is not None else {}),
        )
        if response.status_code != 200:
            raise ValueError(f"HTTP_{response.status_code}")
        payload = response.json()
        if payload.get("code") != "0000":
            raise ValueError("PLATFORM_REQUEST_FAILED")
        return payload.get("data")

    async def login(
        self, user_id: int, origin: str, username: str, password: str
    ) -> dict[str, Any]:
        account = await self.account(user_id)
        async with account.lock:
            account.origin = platform_origin(origin)
            account.token, account.user, account.tenants = "", None, []
            account.client.cookies.clear()
            await self._persist(user_id, account)
            config = await self._request(account, "/api/tenant/config")
            if (
                str(config.get("openCaptcha")) not in {"0", "None", "False", ""}
                and config.get("captchaSceneId")
                and config.get("captchaPrefix")
            ):
                raise ValueError("WEB_VERIFICATION_REQUIRED")
            result = await self._request(
                account,
                "/api/user/passwordLogin",
                {
                    "phoneOrEmail": username.strip(),
                    "areaCode": "86",
                    "password": password,
                    "captchaVerifyParam": "",
                },
            )
            if result.get("stage") == "TENANT_SELECTION_REQUIRED":
                tenants = await self._request(account, "/api/me/tenants")
                account.tenants = [
                    {"id": row["tenantId"], "name": row.get("name", "")}
                    for row in tenants
                    if row.get("status") == "ACTIVE"
                ]
                return account.view()
            await self._accept_token(user_id, account, result)
            return account.view()

    async def _accept_token(self, user_id: int, account: Account, result: dict[str, Any]) -> None:
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("LOGIN_REQUIRED")
        account.token = token
        try:
            user = await self._request(account, "/api/user/getLoginInfo")
            if not user.get("id"):
                raise ValueError("LOGIN_REQUIRED")
            account.user = {
                key: user.get(key) for key in ("id", "username", "userName", "name", "tenantId")
            }
            account.tenants = []
            await self._persist(user_id, account)
        except Exception:
            account.token, account.user = "", None
            raise

    async def tenant(self, user_id: int, tenant_id: int) -> dict[str, Any]:
        account = await self.account(user_id)
        async with account.lock:
            if tenant_id not in {row["id"] for row in account.tenants}:
                raise ValueError("INVALID_TENANT")
            result = await self._request(
                account, "/api/session/tenant/select", {"tenantId": tenant_id}
            )
            await self._accept_token(user_id, account, result)
            return account.view()

    async def devices(self, user_id: int) -> list[dict[str, Any]]:
        account = await self.account(user_id)
        async with account.lock:
            if not account.token:
                raise ValueError("LOGIN_REQUIRED")
            user = await self._request(account, "/api/user/getLoginInfo")
            if not user.get("id"):
                raise ValueError("LOGIN_REQUIRED")
            devices = await self._request(account, "/api/sandbox/config/list")
            account.user = {
                key: user.get(key) for key in ("id", "username", "userName", "name", "tenantId")
            }
            # Never expose device keys, tunnel secrets or other users' records.
            return [
                {key: item.get(key) for key in ("id", "name", "description", "isActive")}
                for item in devices
                if str(item.get("userId")) == str(user.get("id"))
            ]

    async def logout(self, user_id: int) -> dict[str, Any]:
        account = await self.account(user_id)
        async with account.lock:
            account.token, account.user, account.tenants = "", None, []
            account.client.cookies.clear()
            await self._persist(user_id, account)
            return account.view()

    async def shutdown(self) -> None:
        for account in self.accounts.values():
            await account.client.aclose()
