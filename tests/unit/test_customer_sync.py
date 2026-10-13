"""Sync journals use an isolated real SQLite settings store; no cloud calls."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octop.infra.customer_sync.config import entities_from_pack, pack_from_entities
from octop.infra.customer_sync.models import (
    Edit,
    Entity,
    PullResponse,
    PushResponse,
    Resolution,
    Result,
)
from octop.infra.customer_sync.service import CustomerSyncService
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.errors import OctopError
from octop.infra.transfer.archive import decode
from octop.infra.transfer.models import Expert, ModelRef, Package, Provider
from octop.infra.users.identity import User

USER = User(1, "alice", "admin", None)


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    pool = SqlitePool(tmp_path / "sync.db")
    with pool.transaction() as conn:
        conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    yield CustomerSyncService(SimpleNamespace(settings_repo=SettingsRepo(pool)))
    pool.close()


def customer(**kwargs):
    return Edit(
        entity_type="customer",
        entity_id="c",
        payload={"name": "Alice", "unknown": {"keep": True}},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_customer_delete_detaches_children_atomically_and_preserves_payloads(service):
    children = [
        Entity(
            entity_type=kind,
            entity_id=kind,
            revision=i + 2,
            payload={
                "id": kind,
                "title": "",
                "customerId": "c",
                "recordId": "communication",
                "_xfpack": {"keep": True},
                "report": "evidence",
            },
        )
        for i, kind in enumerate(("communication", "followup", "opportunity"))
    ]
    unrelated = Entity(
        entity_type="followup", entity_id="other", revision=5, payload={"customerId": "other"}
    )
    deleted = children[0].model_copy(update={"entity_id": "already-deleted", "deleted": True})
    initial = [
        Entity(entity_type="customer", entity_id="c", revision=1, payload={"name": "Customer"}),
        *children,
        unrelated,
        deleted,
    ]
    await service.ingest(USER, "s", PullResponse(changes=initial, cursor=5, has_more=False))
    state = await service.edit(USER, "s", customer(revision=1, deleted=True))
    assert len(state.pending) == 4
    assert state.pending[-1].entity_type == "customer" and state.pending[-1].deleted
    for before, operation in zip(children, state.pending[:3], strict=True):
        assert operation.entity_id == before.entity_id
        assert operation.base_revision == before.revision and not operation.deleted
        assert operation.payload == {**before.payload, "customerId": ""}
    assert unrelated in state.entities and deleted in state.entities
    assert await CustomerSyncService(service.services).transact(USER, "s") == state


@pytest.mark.asyncio
async def test_customer_delete_preserves_pending_child_and_rolls_back(service):
    await service.ingest(
        USER,
        "s",
        PullResponse(
            changes=[
                Entity(entity_type="customer", entity_id="c", revision=1, payload={"name": "C"}),
                Entity(
                    entity_type="communication",
                    entity_id="clean",
                    revision=2,
                    payload={"title": "Clean", "customerId": "c"},
                ),
            ],
            cursor=2,
            has_more=False,
        ),
    )
    before = await service.edit(
        USER,
        "s",
        Edit(
            entity_type="followup",
            entity_id="pending",
            payload={"title": "Unsent", "customerId": "c"},
        ),
    )
    with pytest.raises(OctopError):
        await service.edit(USER, "s", customer(revision=1, deleted=True))
    assert await service.transact(USER, "s") == before


@pytest.mark.asyncio
async def test_unlinked_child_can_resolve_conflict_without_resurrecting_customer(service):
    state = await service.edit(
        USER,
        "s",
        Edit(
            entity_type="followup", entity_id="f", payload={"title": "Retained", "customerId": ""}
        ),
    )
    op = state.pending[0]
    await service.acknowledge(
        USER,
        "s",
        PushResponse(
            results=[
                Result(
                    operation_id=op.operation_id,
                    status="conflict",
                    revision=3,
                    server_entity=Entity(
                        entity_type="followup",
                        entity_id="f",
                        revision=3,
                        payload={"title": "Remote", "customerId": "c"},
                    ),
                )
            ],
            cursor=3,
        ),
    )
    state = await service.resolve(
        USER, "s", Resolution(operation_id=op.operation_id, choice="local")
    )
    assert state.pending[0].base_revision == 3
    assert state.pending[0].operation_id != op.operation_id
    assert state.pending[0].payload["customerId"] == ""


@pytest.mark.asyncio
async def test_durable_retry_and_scope_isolation(service):
    first = await service.edit(USER, "personal", customer())
    restarted = CustomerSyncService(service.services)
    assert (await restarted.transact(USER, "personal")) == first
    assert not (await restarted.transact(USER, "company")).entities
    assert not (await restarted.transact(User(2, "bob", "admin", None), "personal")).entities
    with pytest.raises(OctopError):
        await service.edit(USER, "personal", customer())
    assert (await restarted.transact(USER, "personal")).pending[0].operation_id == first.pending[
        0
    ].operation_id


@pytest.mark.asyncio
async def test_ack_cursor_must_not_skip_other_devices(service):
    state = await service.edit(USER, "s", customer())
    op = state.pending[0]
    state = await service.acknowledge(
        USER,
        "s",
        PushResponse(
            results=[Result(operation_id=op.operation_id, status="duplicate", revision=7)],
            cursor=99,
        ),
    )
    assert state.cursor == 0 and not state.pending
    assert state.entities[0].revision == 7
    assert state.entities[0].payload["unknown"] == {"keep": True}
    state = await service.ingest(
        USER,
        "s",
        PullResponse(
            changes=[
                Entity(
                    entity_type="customer", entity_id="other", revision=6, payload={"name": "Other"}
                )
            ],
            cursor=99,
            has_more=False,
        ),
    )
    assert len(state.entities) == 2


@pytest.mark.asyncio
async def test_conflict_requires_explicit_resolution(service):
    state = await service.edit(USER, "s", customer())
    op = state.pending[0]
    server = Entity(entity_type="customer", entity_id="c", revision=3, payload={"name": "Remote"})
    state = await service.acknowledge(
        USER,
        "s",
        PushResponse(
            results=[
                Result(
                    operation_id=op.operation_id,
                    status="conflict",
                    revision=3,
                    server_entity=server,
                )
            ],
            cursor=3,
        ),
    )
    assert state.entities[0].payload["name"] == "Alice"
    state = await service.resolve(
        USER, "s", Resolution(operation_id=op.operation_id, choice="local")
    )
    assert state.pending[0].base_revision == 3
    assert state.pending[0].operation_id != op.operation_id
    assert state.pending[0].payload["name"] == "Alice"


@pytest.mark.asyncio
async def test_pull_does_not_clobber_pending_or_regress_revision(service):
    state = await service.edit(USER, "s", customer())
    op_id = state.pending[0].operation_id
    remote = Entity(entity_type="customer", entity_id="c", revision=5, deleted=True)
    state = await service.ingest(
        USER, "s", PullResponse(changes=[remote], cursor=5, has_more=False)
    )
    assert not state.entities[0].deleted and state.pending[0].operation_id == op_id
    with pytest.raises(OctopError):
        await service.ingest(USER, "s", PullResponse(changes=[], cursor=4, has_more=False))
    assert (await service.transact(USER, "s")).cursor == 5


@pytest.mark.asyncio
async def test_ai_requires_confirmation_and_customer_links(service):
    with pytest.raises(OctopError):
        await service.edit(USER, "s", customer(source="ai"))
    assert not (await service.transact(USER, "s")).pending
    await service.edit(USER, "s", customer(source="ai", confirmed=True))
    with pytest.raises(OctopError):
        await service.edit(
            USER,
            "s",
            Edit(
                entity_type="followup",
                entity_id="f",
                payload={"title": "Call", "customerId": "not-a-customer"},
            ),
        )
    state = await service.edit(
        USER,
        "s",
        Edit(entity_type="followup", entity_id="f", payload={"title": "Call", "customerId": "c"}),
    )
    assert len(state.pending) == 2


@pytest.mark.asyncio
async def test_provider_permission_and_secret_opt_in(service):
    edit = Edit(
        entity_type="model_profile",
        entity_id="p",
        payload={
            "name": "Model",
            "key": "secret",
            "apiKey": "android-secret",
            "syncSecret": True,
            "sync_secrets": True,
        },
    )
    with pytest.raises(OctopError):
        await service.edit(User(2, "bob", "user", None), "s", edit)
    state = await service.edit(USER, "s", edit)
    assert "key" not in state.pending[0].payload
    assert "apiKey" not in state.pending[0].payload
    assert state.pending[0].payload["sync_secrets"] is False
    assert state.pending[0].payload["syncSecret"] is False
    state = await service.edit(USER, "other", edit.model_copy(update={"sync_secret": True}))
    assert state.pending[0].payload["key"] == "secret"
    assert state.pending[0].payload["apiKey"] == "android-secret"


@pytest.mark.asyncio
async def test_replayed_conflict_keeps_latest_tombstone_and_new_operation(service):
    state = await service.edit(USER, "s", customer())
    operation_id = state.pending[0].operation_id
    old = Entity(entity_type="customer", entity_id="c", revision=2, payload={"name": "old"})
    response = PushResponse(
        results=[
            Result(
                operation_id=operation_id,
                status="conflict",
                revision=2,
                server_entity=old,
            )
        ],
        cursor=2,
    )
    await service.acknowledge(USER, "s", response)
    tombstone = old.model_copy(update={"revision": 4, "deleted": True, "payload": {}})
    await service.ingest(USER, "s", PullResponse(changes=[tombstone], cursor=4, has_more=False))
    # The server durably replays the original conflict, even after newer deletes.
    for _ in range(3):
        state = await service.acknowledge(USER, "s", response)
        assert state.conflicts[operation_id] == tombstone
    state = await service.resolve(USER, "s", Resolution(operation_id=operation_id, choice="local"))
    assert state.pending[0].operation_id != operation_id
    assert state.pending[0].base_revision == 4
    assert state.pending[0].entity_id == "c"
    assert state.pending[0].payload["unknown"] == {"keep": True}


@pytest.mark.asyncio
async def test_model_tombstone_contains_no_payload_or_credentials(service):
    state = await service.edit(
        USER,
        "s",
        Edit(
            entity_type="model_profile",
            entity_id="m",
            deleted=True,
            sync_secret=True,
            payload={"apiKey": "secret", "sync_secrets": True},
        ),
    )
    assert state.pending[0].payload == {}


def test_xfpack_preserves_metadata_and_never_downloads_keys():
    package = Package(
        package_id="test",
        client_version="1",
        providers=[
            Provider(
                ref="text:p",
                name="P",
                kind="openai",
                models=[{"id": "m"}],
                base_url="https://example.invalid",
            )
        ],
        experts=[
            Expert(
                ref="expert:a",
                name="A",
                system_prompt="prompt",
                engine="dsh",
                model=ModelRef(provider_ref="text:p", model_id="m"),
                dependencies=["plugin:kept"],
                files={"SOUL.md": "assets/a/SOUL.md"},
            )
        ],
    )
    entities = entities_from_pack(
        package, {"assets/a/SOUL.md": b"soul"}, {"text:p": "never-download"}
    )
    restored, files, secrets = decode(pack_from_entities(entities))
    assert restored.experts[0] == package.experts[0]
    assert files["assets/a/SOUL.md"] == b"soul"
    assert not secrets and not restored.providers[0].has_secret


def test_router_openapi_and_auth(service):
    from octop.api.deps import current_user
    from octop.api.routers.customer_sync import router
    from octop.api.routers.customer_sync import service as dependency

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[dependency] = lambda: service
    app.dependency_overrides[current_user] = lambda: USER
    with TestClient(app) as client:
        response = client.get("/api/customer-sync?space_id=s")
        assert response.status_code == 200 and response.json()["entities"] == []
        assert (
            client.post(
                "/api/customer-sync/entities?space_id=s", json=customer().model_dump()
            ).status_code
            == 200
        )
        schema = client.get("/openapi.json").json()
        assert schema["paths"]["/api/customer-sync/entities"]["post"]["summary"]
        assert client.get("/api/customer-sync").status_code == 422


@pytest.mark.asyncio
async def test_lost_ack_retains_newer_remote_changes_after_retry(service):
    state = await service.edit(USER, "s", customer())
    operation_id = state.pending[0].operation_id
    newer = Entity(
        entity_type="customer", entity_id="c", revision=9, payload={"name": "Newer cloud edit"}
    )
    state = await service.ingest(USER, "s", PullResponse(changes=[newer], cursor=9, has_more=False))
    assert state.entities[0].payload["name"] == "Alice"
    state = await service.acknowledge(
        USER,
        "s",
        PushResponse(
            results=[Result(operation_id=operation_id, status="duplicate", revision=5)], cursor=9
        ),
    )
    assert state.entities == [newer] and state.cursor == 9 and not state.pending


@pytest.mark.asyncio
async def test_cloud_tombstone_and_failed_ack_are_atomic(service):
    state = await service.edit(USER, "s", customer())
    operation_id = state.pending[0].operation_id
    with pytest.raises(OctopError):
        await service.acknowledge(
            USER,
            "s",
            PushResponse(
                results=[Result(operation_id=operation_id, status="applied", revision=0)], cursor=9
            ),
        )
    assert (await service.transact(USER, "s")) == state
    state = await service.acknowledge(
        USER,
        "s",
        PushResponse(
            results=[Result(operation_id=operation_id, status="conflict", revision=0)], cursor=0
        ),
    )
    state = await service.resolve(USER, "s", Resolution(operation_id=operation_id, choice="server"))
    assert state.entities[0].deleted and not state.pending


def test_android_skill_edits_keep_other_portable_files():
    package = Package(
        package_id="test",
        client_version="1",
        experts=[
            Expert(
                ref="expert:a",
                name="A",
                files={
                    "skills/test/SKILL.md": "assets/a/SKILL.md",
                    "skills/test/helper.py": "assets/a/helper.py",
                },
            )
        ],
    )
    entities = entities_from_pack(
        package, {"assets/a/SKILL.md": b"original", "assets/a/helper.py": b"helper"}, {}
    )
    next(e for e in entities if e.entity_type == "skill").payload["content"] = "updated on phone"
    _, files, _ = decode(pack_from_entities(entities))
    assert files["assets/a/SKILL.md"] == b"updated on phone"
    assert files["assets/a/helper.py"] == b"helper"


def test_xfpack_keeps_team_bindings_and_models():
    model = ModelRef(provider_ref="text:p", model_id="m")
    package = Package(
        package_id="test",
        client_version="1",
        providers=[
            Provider(
                ref="text:p",
                name="Provider",
                kind="openai",
                base_url="https://example.invalid",
                models=[{"id": "m"}],
            )
        ],
        experts=[
            Expert(ref="expert:a", name="A", system_prompt="A"),
            Expert(ref="expert:b", name="B", system_prompt="B"),
            Expert(
                ref="expert:t",
                name="Team",
                system_prompt="Team",
                kind="team",
                member_refs=["expert:a", "expert:b"],
                member_models={"expert:a": model},
                model=model,
            ),
        ],
    )
    restored, _, _ = decode(pack_from_entities(entities_from_pack(package, {}, {})))
    assert restored.experts == package.experts
    assert restored.providers == package.providers


def test_rejects_payloads_the_server_cannot_accept():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Edit(entity_type="customer", entity_id="c", payload={"name": "a", "bad": float("nan")})
    with pytest.raises(ValidationError):
        Edit(
            entity_type="customer",
            entity_id="c",
            payload={"name": "a", "large": "x" * (2 * 1024 * 1024)},
        )
