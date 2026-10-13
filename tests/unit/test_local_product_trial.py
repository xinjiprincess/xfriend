from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from octop.api.middleware.licensing import LicensingMiddleware
from octop.infra.licensing import local_trial, service
from octop.infra.licensing.local_trial import LocalTrialRecord, LocalTrialStore
from octop.infra.licensing.service import LicenseProfile, LicenseService

DEVICE = "a" * 64
NOW = 1_800_000_000


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    # Tests never touch the real trial directory or registry, including on Windows.
    mirror = {}
    monkeypatch.setattr(local_trial, "local_trial_directory", lambda: tmp_path / "user-license")
    monkeypatch.setattr(LocalTrialStore, "_read_mirror", lambda _self: mirror.get("record"))
    monkeypatch.setattr(
        LocalTrialStore, "_write_mirror", lambda _self, value: mirror.__setitem__("record", value)
    )
    monkeypatch.setattr(service.time, "time", lambda: NOW)

    def protect(value, *, decrypt):
        if decrypt:
            if not value.startswith(b"protected:"):
                raise ValueError("Invalid protected test record")
            return value.removeprefix(b"protected:")
        return b"protected:" + value

    monkeypatch.setattr(local_trial, "_crypt", protect)
    return LocalTrialStore(), mirror, tmp_path / "user-license" / "trial-v1.sqlite3"


def trial_service(home, store, *, days=5, device=DEVICE):
    def unexpected_network(_request):
        pytest.fail("A local trial must not contact any activation server")

    return LicenseService(
        home,
        LicenseProfile(mode="local_trial", trial_days=days),
        required=True,
        device_id=device,
        local_trial_store=store,
        transport=httpx.MockTransport(unexpected_network),
    )


@pytest.mark.asyncio
async def test_five_days_start_once_and_survive_new_install_and_data_directory(
    tmp_path, local_store, monkeypatch
):
    store, _mirror, database = local_store
    first = await trial_service(tmp_path / "old-data", store).status()
    assert first.valid and first.state == "trial"
    assert first.mode == "local_trial" and first.trial_days == 5
    assert first.expires_at == NOW + 5 * 86400
    assert database.is_file()
    monkeypatch.setattr(service.time, "time", lambda: NOW + 4 * 86400)
    restarted = trial_service(tmp_path / "new-data", LocalTrialStore())
    result = await restarted.synchronize(force=True)
    assert result.valid and result.expires_at == first.expires_at
    assert not (tmp_path / "old-data").exists()
    assert not (tmp_path / "new-data").exists()


@pytest.mark.asyncio
async def test_exact_deadline_does_not_restart_the_trial(tmp_path, local_store, monkeypatch):
    store, _, _ = local_store
    client = trial_service(tmp_path, store)
    await client.status()
    monkeypatch.setattr(service.time, "time", lambda: NOW + 5 * 86400 - 1)
    assert (await client.status()).valid
    monkeypatch.setattr(service.time, "time", lambda: NOW + 5 * 86400)
    result = await client.synchronize(force=True)
    assert not result.valid and result.state == "expired"
    restarted = trial_service(tmp_path / "new", LocalTrialStore())
    assert (await restarted.synchronize()).state == "expired"


@pytest.mark.asyncio
async def test_rollback_is_rejected_across_restart(tmp_path, local_store, monkeypatch):
    store, _, _ = local_store
    client = trial_service(tmp_path, store)
    await client.status()
    monkeypatch.setattr(service.time, "time", lambda: NOW + 3600)
    await client.synchronize()
    monkeypatch.setattr(service.time, "time", lambda: NOW)
    result = await trial_service(tmp_path / "new", LocalTrialStore()).status()
    assert result.state == "clock_error" and not result.valid


@pytest.mark.asyncio
async def test_running_process_uses_monotonic_clock_floor(tmp_path, local_store, monkeypatch):
    store, _, _ = local_store
    client = trial_service(tmp_path, store)
    await client.status()
    monkeypatch.setattr(store, "clock_floor", lambda: NOW + 301)
    assert (await client.status()).state == "clock_error"


@pytest.mark.asyncio
async def test_removing_one_anchor_recovers_without_new_trial(tmp_path, local_store, monkeypatch):
    store, mirror, database = local_store
    result = await trial_service(tmp_path / "one", store).status()
    database.unlink()
    monkeypatch.setattr(service.time, "time", lambda: NOW + 86400)
    recovered = await trial_service(tmp_path / "two", LocalTrialStore()).status()
    assert recovered.expires_at == result.expires_at
    mirror.clear()
    recovered = await trial_service(tmp_path / "three", LocalTrialStore()).status()
    assert recovered.expires_at == result.expires_at
    assert mirror["record"]


@pytest.mark.asyncio
@pytest.mark.parametrize("anchor", ["database", "database_type", "mirror"])
async def test_corruption_fails_closed_without_reset(tmp_path, local_store, anchor):
    store, mirror, database = local_store
    await trial_service(tmp_path, store).status()
    if anchor.startswith("database"):
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE trial SET record = ?",
                ("wrong-type" if anchor == "database_type" else b"corrupt",),
            )
    else:
        mirror["record"] = b"corrupt"
    restarted = trial_service(tmp_path / "new", LocalTrialStore())
    result = await restarted.status()
    assert not result.valid and result.state == "invalid"
    assert result.sync_error == "local_storage_error"
    assert not (await restarted.synchronize()).valid


@pytest.mark.asyncio
async def test_wrong_machine_does_not_claim_new_trial(tmp_path, local_store):
    store, _, _ = local_store
    await trial_service(tmp_path, store).status()
    result = await trial_service(tmp_path / "other", LocalTrialStore(), device="b" * 64).status()
    assert not result.valid and result.state == "invalid"


@pytest.mark.asyncio
async def test_later_build_cannot_extend_trial_by_changing_days(tmp_path, local_store):
    store, _, _ = local_store
    first = await trial_service(tmp_path, store).status()
    result = await trial_service(tmp_path / "new", LocalTrialStore(), days=30).status()
    assert result.expires_at == first.expires_at


def test_concurrent_first_claims_share_one_start(local_store):
    _store, _mirror, _database = local_store
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(LocalTrialStore().refresh, DEVICE, 5, NOW + index) for index in range(8)
        ]
        results = [future.result() for future in futures]
    assert len({result.started_at for result in results}) == 1
    assert len({result.expires_at for result in results}) == 1
    final = LocalTrialStore().refresh(DEVICE, 5, NOW)
    assert final.last_seen == NOW + 7


def test_http_business_requests_are_blocked_at_local_trial_deadline(
    tmp_path, local_store, monkeypatch
):
    store, _, _ = local_store
    client = trial_service(tmp_path, store)
    monkeypatch.setitem(service._services, tmp_path.absolute(), client)
    app = FastAPI()
    app.add_middleware(LicensingMiddleware, home=tmp_path)
    executed = []

    @app.post("/api/agents/example/chat")
    async def business_request():
        executed.append(True)
        return {"ok": True}

    with TestClient(app) as browser:
        assert browser.post("/api/agents/example/chat").status_code == 200
        monkeypatch.setattr(service.time, "time", lambda: NOW + 5 * 86400)
        denied = browser.post("/api/agents/example/chat")
        assert denied.status_code == 402 and denied.json()["state"] == "expired"
    assert executed == [True]


@pytest.mark.asyncio
async def test_trial_does_not_modify_server_entitlement_or_user_data(tmp_path, local_store):
    store, _, _ = local_store
    home = tmp_path / "old-data"
    server_record = home / "licensing" / "entitlement.json"
    server_record.parent.mkdir(parents=True)
    server_record.write_text('{"server":"signed-data-unchanged"}', encoding="utf-8")
    user_data = home / "database.sqlite3"
    user_data.write_bytes(b"existing user data")
    before = server_record.read_bytes(), user_data.read_bytes()
    client = trial_service(home, store)
    assert (await client.synchronize()).valid
    assert (server_record.read_bytes(), user_data.read_bytes()) == before


@pytest.mark.asyncio
async def test_local_trial_does_not_accept_activation_codes(tmp_path, local_store):
    store, _, _ = local_store
    client = trial_service(tmp_path, store)
    result = await client.synchronize("made-up-code", force=True)
    assert result.valid and result.sync_error == "local_code_unsupported"
    assert (await client.synchronize()).sync_error is None


@pytest.mark.asyncio
async def test_no_profile_source_development_never_touches_trial_storage(tmp_path, monkeypatch):
    def forbidden_directory():
        pytest.fail("Development must not create or consume a trial")

    monkeypatch.setattr(local_trial, "local_trial_directory", forbidden_directory)
    client = LicenseService(tmp_path, None, required=False)
    result = await client.status()
    assert result.valid and result.mode == "development" and result.trial_days is None


@pytest.mark.asyncio
async def test_storage_permission_failure_is_invalid(tmp_path, local_store, monkeypatch):
    store, _, _ = local_store

    def denied(*_args):
        raise PermissionError("Access denied")

    monkeypatch.setattr(store, "refresh", denied)
    result = await trial_service(tmp_path, store).status()
    assert not result.valid and result.sync_error == "local_storage_error"


@pytest.mark.asyncio
async def test_machine_identity_failure_is_reported_and_recoverable(
    tmp_path, local_store, monkeypatch
):
    store, _, _ = local_store

    def denied():
        raise PermissionError("Registry access denied")

    monkeypatch.setattr(service, "machine_code", denied)
    client = trial_service(tmp_path, store, device="")
    result = await client.status()
    assert not result.valid and result.sync_error == "local_storage_error"
    monkeypatch.setattr(service, "machine_code", lambda: DEVICE)
    assert (await client.synchronize()).valid


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "local_trial"},
        {"mode": "local_trial", "trial_days": 0},
        {"mode": "local_trial", "trial_days": True},
        {"mode": "local_trial", "trial_days": "5"},
        {"mode": "local_trial", "trial_days": 366},
        {"mode": "local_trial", "trial_days": 5, "server_url": "https://example.com"},
        {"mode": "local_trial", "trial_days": 5, "public_key": "unexpected"},
        {"mode": "server", "trial_days": 5},
        {"mode": "unexpected", "trial_days": 5},
        {},
    ],
)
def test_profile_rejects_invalid_or_ambiguous_mode(values):
    with pytest.raises(ValueError):
        LicenseProfile.model_validate(values)


def test_explicit_local_profile_loads_and_cannot_be_disabled(tmp_path, monkeypatch):
    profile = tmp_path / "trial.json"
    profile.write_text(json.dumps({"mode": "local_trial", "trial_days": 5}), encoding="utf-8")
    monkeypatch.setenv("XFRIEND_LICENSE_PROFILE", str(profile))
    loaded, required = service.load_profile()
    assert required and loaded is not None and loaded.trial_days == 5
    profile.write_text('{"mode":"local_trial","trial_days":0}', encoding="utf-8")
    assert service.load_profile() == (None, True)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_real_dpapi_round_trip_and_tampering_without_trial_side_effects():
    # Only exercises CryptProtectData in memory, never creates an actual trial.
    original = (
        LocalTrialRecord(
            device_id=DEVICE, started_at=NOW, expires_at=NOW + 5 * 86400, last_seen=NOW
        )
        .model_dump_json()
        .encode()
    )
    protected = local_trial._crypt(original, decrypt=False)
    assert protected != original
    assert local_trial._crypt(protected, decrypt=True) == original
    with pytest.raises(OSError):
        local_trial._crypt(protected[:-8] + b"tampered", decrypt=True)


@pytest.mark.asyncio
async def test_cancelled_status_waits_for_storage_write(tmp_path, local_store, monkeypatch):
    import threading

    store, _, _ = local_store
    entered, release = threading.Event(), threading.Event()
    real_refresh = store.refresh

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return real_refresh(*args)

    monkeypatch.setattr(store, "refresh", slow)
    client = trial_service(tmp_path, store)
    pending = asyncio.create_task(client.status())
    await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
    pending.cancel()
    await asyncio.sleep(0)
    assert not pending.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert (await trial_service(tmp_path / "restart", LocalTrialStore()).status()).valid
