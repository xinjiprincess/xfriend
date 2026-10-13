"""Verify server entitlements or an explicitly packaged offline trial policy."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import platform
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing.local_trial import LocalTrialRecord, LocalTrialStore
from octop.infra.utils.async_io import finish_io

PRODUCT = "singu-xfriend"
SYNC_INTERVAL = 6 * 60 * 60


def decode_base64(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class LicenseProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["server", "local_trial"] = "server"
    server_url: str = ""
    public_key: str = ""
    trial_days: int | None = Field(default=None, strict=True, ge=1, le=365)
    cloud_accounts: bool = False

    @model_validator(mode="after")
    def validate_mode(self) -> LicenseProfile:
        if self.mode == "local_trial":
            if self.trial_days is None or self.server_url or self.public_key or self.cloud_accounts:
                raise ValueError("Local trials require days and no server credentials")
        elif self.trial_days is not None or not self.server_url or not self.public_key:
            raise ValueError("Server licensing requires an endpoint and verification key")
        return self

    def validate_release(self) -> None:
        LicenseProfile.model_validate(self.model_dump())
        if self.mode == "local_trial":
            return
        url = urlsplit(self.server_url)
        local = url.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (url.scheme != "https" and not (local and url.scheme == "http"))
        ):
            raise ValueError("Activation server must use HTTPS (loopback allowed for development)")
        Ed25519PublicKey.from_public_bytes(decode_base64(self.public_key))


class SignedLicense(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: str = Field(max_length=8192)
    signature: str = Field(max_length=128)


class Entitlement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1, 2]
    license_id: str
    product: Literal["singu-xfriend"]
    device_id: str
    edition: Literal["trial", "full"]
    issued_at: int
    not_before: int
    expires_at: int
    user_id: str = ""
    space_id: str = ""
    offline_until: int | None = None
    display_name: str = ""
    identifier: str = ""


class LicenseStatus(BaseModel):
    required: bool
    valid: bool
    state: str
    mode: Literal["server", "local_trial", "development"] = "server"
    trial_days: int | None = None
    device_id: str = ""
    edition: str | None = None
    expires_at: int | None = None
    last_synced_at: int | None = None
    sync_error: str | None = None
    timezone: str = "Asia/Shanghai"


def machine_code() -> str:
    """Hash stable OS identity; never send registry identifiers or network addresses."""
    identity = ""
    if os.name == "nt":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            identity = str(winreg.QueryValueEx(key, "MachineGuid")[0])
    else:
        for source in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            if source.is_file():
                identity = source.read_text().strip()
                break
        if not identity:
            identity = f"{platform.system()}:{platform.node()}:{uuid.getnode()}"
    if not identity.strip():
        raise ValueError("Machine identity is unavailable")
    return hashlib.sha256(f"{PRODUCT}:v1:{identity.strip()}".encode()).hexdigest()


def load_profile() -> tuple[LicenseProfile | None, bool]:
    # Release builds embed this public profile. A user data file cannot disable it.
    embedded = Path(__file__).with_name("release-profile.json")
    override = os.environ.get("XFRIEND_LICENSE_PROFILE", "")
    source = embedded if embedded.exists() else Path(override) if override else None
    if source is None:
        return None, False  # Source development; release packaging requires a profile.
    try:
        profile = LicenseProfile.model_validate_json(source.read_text(encoding="utf-8"))
        profile.validate_release()
        return profile, True
    except (OSError, ValueError):
        return None, True  # A broken release configuration must fail closed.


class LicenseService:
    def __init__(
        self,
        home: Path,
        profile: LicenseProfile | None,
        *,
        required: bool,
        device_id: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
        local_trial_store: LocalTrialStore | None = None,
    ) -> None:
        self.path = home / "licensing" / "entitlement.json"
        self.profile = profile
        self.required = required
        self.device_id = device_id
        self.transport = transport
        self.lock = asyncio.Lock()
        self.sync_lock = asyncio.Lock()
        self.record: dict[str, Any] = {}
        self.loaded = False
        self.sync_error: str | None = None
        self.next_attempt = 0.0
        self.last_write = 0.0
        self.local_trial_store = local_trial_store or LocalTrialStore()
        self.local_record: LocalTrialRecord | None = None

    def _is_local_trial(self) -> bool:
        return self.profile is not None and self.profile.mode == "local_trial"

    async def _refresh_local_trial(self) -> None:
        if self.profile is None or self.profile.trial_days is None:
            return
        days = self.profile.trial_days

        def refresh() -> LocalTrialRecord:
            if not self.device_id:
                self.device_id = machine_code()
            return self.local_trial_store.refresh(self.device_id, days, int(time.time()))

        try:
            self.local_record = await finish_io(refresh)
            self.sync_error = None
        except (OSError, ValueError, sqlite3.Error):
            self.local_record = None
            self.sync_error = "local_storage_error"
        self.last_write = time.monotonic()

    async def _load(self) -> None:
        if self.loaded:
            return
        if not self.required:
            self.loaded = True
            return

        def read() -> None:
            if self._is_local_trial():
                return
            if self.required and not self.device_id:
                self.device_id = machine_code()
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and all(
                        key not in data
                        or (isinstance(data[key], int) and not isinstance(data[key], bool))
                        for key in ("last_seen", "last_synced_at")
                    ):
                        self.record = data
                except (OSError, ValueError):
                    self.record = {}

        await asyncio.get_running_loop().run_in_executor(None, read)
        if self._is_local_trial():
            await self._refresh_local_trial()
        self.loaded = True

    async def _save(self) -> None:
        payload = json.dumps(self.record, ensure_ascii=False)

        def write() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            pending = self.path.with_suffix(".tmp")
            pending.write_text(payload, encoding="utf-8")
            pending.replace(self.path)

        await finish_io(write)
        self.last_write = time.monotonic()

    def _verify(self, value: object) -> Entitlement:
        if self.profile is None or self.profile.mode != "server":
            raise ValueError("Missing licensing profile")
        signed = SignedLicense.model_validate(value)
        data = decode_base64(signed.payload)
        Ed25519PublicKey.from_public_bytes(decode_base64(self.profile.public_key)).verify(
            decode_base64(signed.signature), data
        )
        entitlement = Entitlement.model_validate_json(data)
        if not self.profile.cloud_accounts and entitlement.schema_version != 1:
            raise ValueError("Invalid machine entitlement schema")
        if self.profile.cloud_accounts and (
            entitlement.schema_version != 2
            or not entitlement.user_id
            or not entitlement.space_id
            or entitlement.offline_until is None
            or entitlement.offline_until
            > min(entitlement.expires_at, entitlement.issued_at + 72 * 3600)
        ):
            raise ValueError("Invalid account entitlement")
        if (
            entitlement.device_id != self.device_id
            or entitlement.expires_at <= entitlement.not_before
            or entitlement.issued_at < entitlement.not_before
        ):
            raise ValueError("Invalid machine entitlement")
        return entitlement

    def _status(self) -> LicenseStatus:
        base = LicenseStatus(
            required=self.required,
            valid=not self.required,
            state="development" if not self.required else "unactivated",
            mode=(
                "development"
                if not self.required
                else self.profile.mode
                if self.profile
                else "server"
            ),
            trial_days=self.profile.trial_days if self.profile else None,
            device_id=self.device_id,
            sync_error=self.sync_error,
            last_synced_at=self.record.get("last_synced_at"),
        )
        if not self.required:
            return base
        if self.profile is None:
            base.state = "unconfigured"
            return base
        if self._is_local_trial():
            record = self.local_record
            if record is None:
                base.state = "invalid"
                return base
            base.edition, base.expires_at = "trial", record.expires_at
            now = int(time.time())
            if now + 300 < max(record.last_seen, self.local_trial_store.clock_floor()):
                base.state = "clock_error"
            elif now >= record.expires_at:
                base.state = "expired"
            else:
                base.valid, base.state = True, "trial"
            return base
        if self.record.get("revoked"):
            base.state = "revoked"
            return base
        if "license" not in self.record:
            return base
        try:
            entitlement = self._verify(self.record["license"])
        except (ValueError, InvalidSignature, ValidationError):
            base.state = "invalid"
            return base
        now = int(time.time())
        base.edition, base.expires_at = entitlement.edition, entitlement.expires_at
        if entitlement.offline_until is not None and now >= entitlement.offline_until:
            base.state = "offline_expired"
            return base
        if now + 300 < max(entitlement.issued_at, int(self.record.get("last_seen", 0))):
            base.state = "clock_error"
        elif now < entitlement.not_before:
            base.state = "not_started"
        elif now >= entitlement.expires_at:
            base.state = "expired"
        else:
            base.valid, base.state = True, entitlement.edition
        return base

    async def status(self) -> LicenseStatus:
        async with self.lock:
            await self._load()
            if self._is_local_trial() and time.monotonic() - self.last_write > 60:
                await self._refresh_local_trial()
            result = self._status()
            if (
                result.valid
                and self.required
                and not self._is_local_trial()
                and time.monotonic() - self.last_write > 60
            ):
                self.record["last_seen"] = max(
                    int(time.time()), int(self.record.get("last_seen", 0))
                )
                await self._save()
            return result

    async def synchronize(self, code: str | None = None, *, force: bool = False) -> LicenseStatus:
        async with self.sync_lock:
            async with self.lock:
                await self._load()
                if not self.required or self.profile is None:
                    return self._status()
                if self._is_local_trial():
                    await self._refresh_local_trial()
                    if code:
                        self.sync_error = "local_code_unsupported"
                    return self._status()
                if self.profile.cloud_accounts:
                    # The desktop account bridge owns credential rotation and lease refresh.
                    return self._status()
                if not code and not force and time.monotonic() < self.next_attempt:
                    return self._status()
                self.next_attempt = time.monotonic() + 60
                endpoint = "trial"
                body: dict[str, Any] = {"device_id": self.device_id, "product": PRODUCT}
                if code:
                    endpoint, body = "activate", {**body, "code": code}
                elif self.record.get("license"):
                    try:
                        self._verify(self.record["license"])
                    except (ValueError, InvalidSignature):
                        pass  # Recover this machine's server record, never restart its trial.
                    else:
                        endpoint, body = "refresh", {"license": self.record["license"]}
                server_url = self.profile.server_url
            try:
                async with httpx.AsyncClient(
                    timeout=15, transport=self.transport, follow_redirects=False
                ) as client:
                    response = await client.post(
                        server_url.rstrip("/") + f"/api/v1/{endpoint}", json=body
                    )
                if response.status_code == 403 and response.json().get("code") == "license_revoked":
                    async with self.lock:
                        self.record["revoked"] = True
                        await self._save()
                        self.sync_error = "revoked"
                        return self._status()
                response.raise_for_status()
                if len(response.content) > 32_768:
                    raise ValueError("Oversized activation response")
                data = response.json()
                entitlement = self._verify(data["license"])
                now = int(time.time())
                async with self.lock:
                    self.record = {
                        "license": SignedLicense.model_validate(data["license"]).model_dump(),
                        "last_synced_at": now,
                        "last_seen": max(now, entitlement.issued_at),
                    }
                    await self._save()
                    self.sync_error = None
                    self.next_attempt = time.monotonic() + SYNC_INTERVAL
            except httpx.HTTPStatusError as exc:
                self.sync_error = (
                    "code_rejected"
                    if code and exc.response.status_code in {400, 403, 404, 409, 422}
                    else "server_rejected"
                )
                if exc.response.status_code == 429:
                    self.sync_error = "rate_limited"
                elif exc.response.is_server_error:
                    self.sync_error = "unavailable"
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
                InvalidSignature,
            ):
                self.sync_error = "unavailable"
            return self._status()


_services: dict[Path, LicenseService] = {}


def product_license(home: Path) -> LicenseService:
    home = home.absolute()
    if home not in _services:
        profile, required = load_profile()
        _services[home] = LicenseService(home, profile, required=required)
    return _services[home]


async def require_product_license(home: Path) -> None:
    status = await product_license(home).status()
    if not status.valid:
        raise OctopError(ErrorCode.LICENSE_REQUIRED, "Product activation is required", status=402)
