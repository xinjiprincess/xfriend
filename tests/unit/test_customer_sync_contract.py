"""Optional sibling-server contract tests; no network or Android build.

Run with the activation server's src on PYTHONPATH. All cloud state is temporary.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from octop.infra.customer_sync.models import Edit, PullResponse, PushResponse, Resolution
from octop.infra.customer_sync.service import CustomerSyncService
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.users.identity import User

USER = User(1, "contract", "admin", None)


@pytest.fixture
def peers(tmp_path, monkeypatch):
    create_app = pytest.importorskip("xfriend_activation.app").create_app
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "desktop"))
    app = create_app(tmp_path / "cloud")
    accounts = app.state.accounts
    accounts.licensing.clock = lambda: 1800000000
    session = accounts.register("13812345678", "sync@example.com", "Sync", "test", "a" * 64)
    pool = SqlitePool(tmp_path / "desktop.db")
    with pool.transaction() as conn:
        conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    desktop = CustomerSyncService(SimpleNamespace(settings_repo=SettingsRepo(pool)))
    try:
        with TestClient(app, base_url="http://127.0.0.1:9097") as client:
            client.headers["Authorization"] = "Bearer " + session["access_token"]
            yield desktop, client, session["space_id"], accounts
    finally:
        pool.close()


def push(client, space, operations):
    response = client.post(
        "/api/v2/sync/push",
        json={
            "space_id": space,
            "device_id": "a" * 64,
            "operations": operations,
        },
    )
    assert response.status_code == 200, response.text
    return PushResponse.model_validate(response.json())


async def pull(desktop, client, space):
    state = await desktop.transact(USER, space)
    response = client.get(
        "/api/v2/sync/pull",
        params={
            "space_id": space,
            "cursor": state.cursor,
            "limit": 100,
        },
    )
    assert response.status_code == 200, response.text
    return await desktop.ingest(USER, space, PullResponse.model_validate(response.json()))


@pytest.mark.asyncio
async def test_actual_server_customer_delete_syncs_retained_unlinked_children(peers):
    desktop, client, space, _ = peers
    await desktop.edit(
        USER, space, Edit(entity_type="customer", entity_id="c", payload={"name": "C"})
    )
    for kind in ("communication", "followup", "opportunity"):
        await desktop.edit(
            USER,
            space,
            Edit(
                entity_type=kind,
                entity_id=kind,
                payload={
                    "title": kind,
                    "customerId": "c",
                    "recordId": "communication",
                    "extra": "keep",
                },
            ),
        )
    state = await desktop.transact(USER, space)
    await desktop.acknowledge(
        USER, space, push(client, space, [op.model_dump() for op in state.pending])
    )
    state = await pull(desktop, client, space)
    customer = next(e for e in state.entities if e.entity_type == "customer")
    state = await desktop.edit(
        USER, space, Edit(**customer.model_dump(exclude={"deleted"}), deleted=True)
    )
    operations = [op.model_dump() for op in state.pending]
    assert len(operations) == 4
    # Retry the complete batch after losing its receipt; no duplicate entities.
    push(client, space, operations)
    await desktop.acknowledge(USER, space, push(client, space, operations))
    state = await pull(desktop, client, space)
    assert not state.pending and len(state.entities) == 4
    for entity in state.entities:
        if entity.entity_type == "customer":
            assert entity.deleted
        else:
            assert not entity.deleted and entity.payload["customerId"] == ""
            assert entity.payload["recordId"] == "communication"
            assert entity.payload["extra"] == "keep"


@pytest.mark.asyncio
async def test_actual_server_lost_ack_tombstone_conflict_cycle(peers):
    desktop, client, space, _ = peers
    state = await desktop.edit(
        USER,
        space,
        Edit(
            entity_type="customer",
            entity_id="android-stable-id",
            payload={"id": "wrong-payload-id", "name": "Local", "extension": {"retain": True}},
        ),
    )
    original = state.pending[0].model_dump()
    assert original["payload"]["id"] == "android-stable-id"
    assert push(client, space, [original]).results[0].revision == 1
    remote = {
        **original,
        "operation_id": "phone-delete",
        "base_revision": 1,
        "deleted": True,
        "payload": {},
    }
    push(client, space, [remote])
    await pull(desktop, client, space)
    duplicate = push(client, space, [original])
    assert duplicate.results[0].status == "duplicate"
    state = await desktop.acknowledge(USER, space, duplicate)
    assert not state.pending and state.entities[0].deleted and state.entities[0].revision == 2

    state = await desktop.edit(
        USER,
        space,
        Edit(
            entity_type="customer",
            entity_id="android-stable-id",
            revision=2,
            payload=original["payload"],
        ),
    )
    local = state.pending[0].model_dump()
    push(client, space, [{**local, "operation_id": "phone-restore", "payload": {"name": "Phone"}}])
    conflict = push(client, space, [local])
    await desktop.acknowledge(USER, space, conflict)
    push(client, space, [{**remote, "operation_id": "phone-delete-again", "base_revision": 3}])
    await pull(desktop, client, space)
    for _ in range(3):
        state = await desktop.acknowledge(USER, space, push(client, space, [local]))
        assert state.conflicts[local["operation_id"]].revision == 4
    state = await desktop.resolve(
        USER, space, Resolution(operation_id=local["operation_id"], choice="local")
    )
    retry = state.pending[0]
    assert retry.base_revision == 4 and retry.operation_id != local["operation_id"]
    await desktop.acknowledge(USER, space, push(client, space, [retry.model_dump()]))
    state = await pull(desktop, client, space)
    assert not state.pending and not state.conflicts
    assert len(state.entities) == 1 and state.entities[0].revision == 5
    assert state.entities[0].payload["extension"] == {"retain": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("opt_in", [False, True])
async def test_actual_server_android_secret_alias_and_xfpack(peers, opt_in):
    desktop, client, space, accounts = peers
    metadata = {
        "format_version": 1,
        "resource": {"engine": "dsh", "preferences": {"tools": False}},
        "files": {"skills/a/helper.py": "cHJpbnQoMSk="},
        "providers": [{"ref": "text:p"}],
    }
    state = await desktop.edit(
        USER,
        space,
        Edit(
            entity_type="model_profile",
            entity_id="android-model-id",
            sync_secret=opt_in,
            payload={
                "name": "Model",
                "apiKey": "contract-secret-marker",
                "sync_secrets": True,
                "_xfpack": metadata,
            },
        ),
    )
    await desktop.acknowledge(USER, space, push(client, space, [state.pending[0].model_dump()]))
    state = await pull(desktop, client, space)
    payload = state.entities[0].payload
    assert payload["_xfpack"] == metadata
    assert ("apiKey" in payload) is opt_in
    assert payload["sync_secrets"] is opt_in
    # Full server records (including rejected-operation proposals) must not contain plaintext.
    with accounts.connection() as db:
        for table in ("sync_changes", "sync_operations"):
            rows = db.execute(f"SELECT * FROM {table}").fetchall()
            assert rows
            raw = repr([tuple(row) for row in rows])
            assert "contract-secret-marker" not in raw
            assert "helper.py" not in raw
