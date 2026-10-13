from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient

from octop.api.middleware.licensing import LicensingMiddleware
from octop.infra.licensing import service
from octop.infra.licensing.service import LicenseProfile, LicenseService


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.fixture
def issuer():
    key = Ed25519PrivateKey.generate()
    profile = LicenseProfile(
        server_url="https://license.example.test/xfriendmanager",
        public_key=b64(
            key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ),
    )

    def sign(*, device="a" * 64, expires=None, edition="trial"):
        now = int(time.time())
        payload = json.dumps(
            {
                "schema_version": 1,
                "license_id": "test-license",
                "product": "singu-xfriend",
                "device_id": device,
                "edition": edition,
                "issued_at": now - 10,
                "not_before": now - 30,
                "expires_at": expires if expires is not None else now + 5 * 86400,
            }
        ).encode()
        return {"payload": b64(payload), "signature": b64(key.sign(payload))}

    return profile, sign


@pytest.mark.asyncio
async def test_trial_restart_and_offline_verification(tmp_path, issuer):
    profile, sign = issuer
    calls = []

    def activate(request):
        calls.append(request.url.path)
        assert json.loads(request.content)["device_id"] == "a" * 64
        return httpx.Response(200, json={"license": sign(), "server_time": int(time.time())})

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(activate),
    )
    assert (await client.synchronize()).state == "trial"
    assert calls == ["/xfriendmanager/api/v1/trial"]
    await client.synchronize()
    assert len(calls) == 1  # Automatic refresh does not hammer the server.

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    restarted = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(offline),
    )
    result = await restarted.synchronize()
    assert result.valid and result.sync_error == "unavailable"


@pytest.mark.asyncio
async def test_wrong_machine_forgery_and_expiration_are_rejected(tmp_path, issuer):
    profile, sign = issuer
    client = LicenseService(tmp_path, profile, required=True, device_id="a" * 64)
    client.loaded = True
    client.record = {"license": sign(device="b" * 64)}
    assert (await client.status()).state == "invalid"
    client.record = {"license": sign()}
    client.record["license"]["signature"] = b64(bytes(64))
    assert not (await client.status()).valid
    client.record = {"license": sign(expires=int(time.time()) - 1)}
    assert (await client.status()).state == "expired"


@pytest.mark.asyncio
async def test_revocation_persists_and_admin_renewal_recovers(tmp_path, issuer):
    profile, sign = issuer
    response = httpx.Response(403, json={"code": "license_revoked"})

    def respond(_request):
        return response

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(respond),
    )
    client.loaded = True
    client.record = {"license": sign()}
    assert (await client.synchronize()).state == "revoked"
    copy = LicenseService(tmp_path, profile, required=True, device_id="a" * 64)
    assert (await copy.status()).state == "revoked"
    response = httpx.Response(200, json={"license": sign(edition="full")})
    assert (await client.synchronize(force=True)).state == "full"


@pytest.mark.asyncio
async def test_server_expired_license_does_not_restart_trial(tmp_path, issuer):
    profile, sign = issuer
    expired = sign(expires=int(time.time()) - 1)
    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"license": expired})),
    )
    result = await client.synchronize()
    assert not result.valid and result.state == "expired"
    assert client.record["license"] == expired


@pytest.mark.asyncio
async def test_clock_rollback_and_invalid_profile_fail_closed(tmp_path, issuer):
    profile, sign = issuer
    client = LicenseService(tmp_path, profile, required=True, device_id="a" * 64)
    client.loaded = True
    client.record = {"license": sign(), "last_seen": int(time.time()) + 3600}
    assert (await client.status()).state == "clock_error"
    bad = LicenseService(tmp_path / "bad", None, required=True, device_id="a" * 64)
    assert not (await bad.synchronize()).valid
    assert (await bad.status()).state == "unconfigured"


def test_release_profile_rejects_untrusted_transport(issuer):
    profile, _ = issuer
    profile.validate_release()
    for url in ("http://remote.example", "https://user:password@host", "file:///test"):
        profile.server_url = url
        with pytest.raises(ValueError):
            profile.validate_release()


def test_license_gate_covers_http_and_websocket_but_keeps_setup(tmp_path, issuer, monkeypatch):
    profile, _ = issuer
    client = LicenseService(tmp_path, profile, required=True, device_id="a" * 64)
    monkeypatch.setitem(service._services, Path(tmp_path).absolute(), client)
    app = FastAPI()
    app.add_middleware(LicensingMiddleware, home=tmp_path)

    @app.get("/api/agents")
    async def protected():
        return {"ok": True}

    @app.get("/api/setup/status")
    async def setup():
        return {"ok": True}

    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as browser:
        assert browser.get("/api/agents").status_code == 402
        assert browser.get("/api/setup/status").status_code == 200
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            browser.websocket_connect("/api/agents/test/chat/ws"),
        ):
            pass
        assert closed.value.code == 4402


def test_embedded_profile_cannot_be_disabled_by_missing_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XFRIEND_LICENSE_PROFILE", str(tmp_path / "missing.json"))
    profile, required = service.load_profile()
    assert required and profile is None


@pytest.mark.asyncio
async def test_slow_online_refresh_does_not_block_valid_offline_license(tmp_path, issuer):
    profile, sign = issuer
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(_request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"license": sign()})

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(slow),
    )
    client.loaded = True
    client.record = {"license": sign()}
    pending = asyncio.create_task(client.synchronize())
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert (await asyncio.wait_for(client.status(), 2)).valid
    finally:
        release.set()
        await pending


def test_connected_websocket_stops_accepting_commands_after_expiry(tmp_path, issuer, monkeypatch):
    profile, sign = issuer
    license = LicenseService(tmp_path, profile, required=True, device_id="a" * 64)
    license.loaded = True
    license.record = {"license": sign()}
    monkeypatch.setitem(service._services, Path(tmp_path).absolute(), license)
    app = FastAPI()
    app.add_middleware(LicensingMiddleware, home=tmp_path)
    commands = []

    @app.websocket("/api/terminal/ws")
    async def terminal(socket: WebSocket):
        await socket.accept()
        while True:
            packet = await socket.receive()
            if packet["type"] == "websocket.disconnect":
                return
            commands.append(packet["text"])
            await socket.send_text("ok")

    with TestClient(app) as browser, browser.websocket_connect("/api/terminal/ws") as socket:
        socket.send_text("first")
        assert socket.receive_text() == "ok"
        license.record = {"license": sign(expires=int(time.time()) - 1)}
        socket.send_text("second")
        assert socket.receive() == {"type": "websocket.close", "code": 4402}
    assert commands == ["first"]


@pytest.mark.asyncio
async def test_invalid_local_certificate_recovers_machine_record_online(tmp_path, issuer):
    profile, sign = issuer
    observed = []

    def respond(request):
        observed.append(request.url.path)
        return httpx.Response(200, json={"license": sign(edition="full")})

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(respond),
    )
    client.loaded = True
    client.record = {"license": {"payload": "broken", "signature": "broken"}}
    assert (await client.synchronize()).state == "full"
    assert observed == ["/xfriendmanager/api/v1/trial"]


@pytest.mark.asyncio
async def test_server_synchronizes_without_a_browser_and_stops_cleanly(
    tmp_path, issuer, monkeypatch
):
    from octop.infra.server import OctopServer

    profile, sign = issuer
    contacted = asyncio.Event()

    def respond(_request):
        contacted.set()
        return httpx.Response(200, json={"license": sign()})

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(respond),
    )
    server = OctopServer(home=tmp_path)

    async def start_runtime():
        server._started = True

    monkeypatch.setattr(server, "_start", start_runtime)
    monkeypatch.setattr(service, "load_profile", lambda: (profile, True))
    monkeypatch.setattr(service, "product_license", lambda _home: client)
    await server.start()
    try:
        await asyncio.wait_for(contacted.wait(), 2)
        assert server._license_sync_task is not None
    finally:
        await server.stop()
    assert server._license_sync_task is None


@pytest.mark.asyncio
async def test_corrupt_cache_metadata_can_be_recovered_online(tmp_path, issuer):
    profile, sign = issuer
    target = tmp_path / "licensing" / "entitlement.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"license": sign(), "last_seen": "broken"}), encoding="utf-8")
    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"license": sign()})),
    )
    assert not (await client.status()).valid
    assert (await client.synchronize()).valid


@pytest.mark.asyncio
async def test_first_launch_offline_does_not_grant_an_unsigned_trial(tmp_path, issuer):
    profile, _ = issuer

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(offline),
    )
    result = await client.synchronize()
    assert not result.valid and result.state == "unactivated"
    assert result.sync_error == "unavailable"
    assert not client.path.exists()


@pytest.mark.asyncio
async def test_admin_renewal_refreshes_same_machine_and_preserves_user_data(tmp_path, issuer):
    profile, sign = issuer
    old_license = sign()
    renewed = sign(expires=int(time.time()) + 365 * 86400, edition="full")
    user_data = tmp_path / "existing-conversation.json"
    user_data.write_text('{"message":"keep my conversation"}', encoding="utf-8")
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert json.loads(request.content) == {"license": old_license}
        return httpx.Response(200, json={"license": renewed})

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(respond),
    )
    client.loaded = True
    client.record = {"license": old_license}
    result = await client.synchronize(force=True)
    assert result.valid and result.state == "full"
    assert calls == ["/xfriendmanager/api/v1/refresh"]
    restarted = LicenseService(tmp_path, profile, required=True, device_id="a" * 64)
    restored = await restarted.status()
    assert restored.valid and restored.expires_at == result.expires_at
    assert user_data.read_text(encoding="utf-8") == '{"message":"keep my conversation"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (403, "code_revoked", "code_rejected"),
        (409, "code_used", "code_rejected"),
        (429, "rate_limited", "rate_limited"),
        (503, "maintenance", "unavailable"),
    ],
)
async def test_failed_code_redemption_retains_existing_license(
    tmp_path, issuer, status, error, expected
):
    profile, sign = issuer

    def respond(request):
        assert request.url.path == "/xfriendmanager/api/v1/activate"
        assert json.loads(request.content) == {
            "device_id": "a" * 64,
            "product": "singu-xfriend",
            "code": "test-activation-code",
        }
        return httpx.Response(status, json={"code": error})

    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(respond),
    )
    client.loaded = True
    client.record = {"license": sign()}
    original = client.record.copy()
    result = await client.synchronize("test-activation-code", force=True)
    assert result.valid and result.sync_error == expected
    assert client.record == original


@pytest.mark.asyncio
async def test_redemption_observes_device_revocation_and_persists_it(tmp_path, issuer):
    profile, sign = issuer
    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(403, json={"code": "license_revoked"})
        ),
    )
    client.loaded = True
    client.record = {"license": sign()}
    result = await client.synchronize("test-activation-code", force=True)
    assert not result.valid and result.state == "revoked"
    restarted = LicenseService(tmp_path, profile, required=True, device_id="a" * 64)
    assert (await restarted.status()).state == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["wrong_machine", "forged_signature"])
async def test_invalid_online_response_cannot_replace_verified_license(tmp_path, issuer, problem):
    profile, sign = issuer
    invalid = sign(device="b" * 64) if problem == "wrong_machine" else sign()
    if problem == "forged_signature":
        invalid["signature"] = b64(bytes(64))
    client = LicenseService(
        tmp_path,
        profile,
        required=True,
        device_id="a" * 64,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"license": invalid})),
    )
    client.loaded = True
    original = sign()
    client.record = {"license": original}
    result = await client.synchronize(force=True)
    assert result.valid and result.sync_error == "unavailable"
    assert client.record["license"] == original
