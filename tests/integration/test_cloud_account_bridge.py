from __future__ import annotations

import base64
import hashlib
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.licensing.service import machine_code
from tests.support.app import octop_client


@pytest.mark.asyncio
async def test_cloud_first_login_binds_database_and_local_identity(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    origin = "https://accounts.example.com"
    space_key = hashlib.sha256((origin + "\x00user\x00personal").encode()).hexdigest()
    home = tmp_path / space_key
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"cloud_accounts": True, "server_url": origin, "public_key": encode(public)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XFRIEND_LICENSE_PROFILE", str(profile))
    monkeypatch.setenv("OCTOP_DATABASE_DRIVER", "sqlite")
    monkeypatch.setenv("OCTOP_DATABASE_SQLITE_PATH", str(home / "xfriend.db"))
    monkeypatch.setenv("OCTOP_REQUIRE_SETUP_PASSWORD", "false")
    monkeypatch.setenv("XFRIEND_CLOUD_BRIDGE_SECRET", "test-bridge")
    monkeypatch.setenv("XFRIEND_CLOUD_SPACE_KEY", space_key)
    now = int(time.time())
    payload = json.dumps(
        {
            "schema_version": 2,
            "product": "singu-xfriend",
            "license_id": "session",
            "user_id": "user",
            "space_id": "personal",
            "device_id": machine_code(),
            "edition": "trial",
            "issued_at": now,
            "not_before": now - 1,
            "expires_at": now + 5 * 86400,
            "offline_until": now + 72 * 3600,
            "display_name": "测试用户",
            "identifier": "+8613812345678",
        }
    ).encode()
    lease = {"payload": encode(payload), "signature": encode(key.sign(payload))}
    async with octop_client(home, bind_database=False) as (client, server):
        assert server.database_bound
        assert server.user_manager.count() == 0
        denied = await client.post("/api/auth/cloud-session", json={"lease": lease})
        assert denied.status_code == 401
        accepted = await client.post(
            "/api/auth/cloud-session",
            json={"lease": lease},
            headers={"X-Xfriend-Cloud-Bridge": "test-bridge"},
        )
        assert accepted.status_code == 200, accepted.text
        token = accepted.json()["access_token"]
        me = await client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
        assert me.status_code == 200, me.text
        assert me.json()["username"].startswith("cloud_")
        assert server.user_manager.count() == 1
