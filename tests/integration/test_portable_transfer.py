"""Portable HTTP workflows with isolated databases and no provider network calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octop.infra.agents.manager import AgentCreateSpec
from octop.infra.transfer.archive import decode, encode
from octop.infra.transfer.models import Expert, ModelRef, Package, Provider
from octop.infra.users.identity import User
from tests.support.auth import create_user


def sample(*, secret: bool = False, team: bool = False) -> bytes:
    experts = [
        Expert(
            ref="expert:a",
            name="写作",
            model=ModelRef(provider_ref="text:p", model_id="test/model"),
            files={"SOUL.md": "assets/a/SOUL.md"},
        ),
        Expert(
            ref="expert:b",
            name="审核",
            model=ModelRef(provider_ref="text:p", model_id="test/model"),
        ),
    ]
    if team:
        experts.append(
            Expert(
                ref="expert:team",
                name="写作团队",
                kind="team",
                system_prompt="先写作，再交叉审核",
                member_refs=["expert:a", "expert:b"],
                member_models={"expert:a": ModelRef(provider_ref="text:p", model_id="test/model")},
                model=ModelRef(provider_ref="text:p", model_id="test/model"),
            )
        )
    package = Package(
        package_id="fixture",
        client_version="1.4.7",
        experts=experts,
        providers=[
            Provider(
                ref="text:p",
                name="测试模型",
                kind="openai",
                base_url="https://example.invalid/v1",
                models=[{"id": "test/model", "input": ["text"], "enabled": True}],
                has_secret=secret,
            )
        ],
    )
    return encode(
        package,
        {"assets/a/SOUL.md": "只用于本地测试".encode()},
        {"text:p": "temporary-key"} if secret else {},
        "passphrase" if secret else "",
    )


async def preview(
    client: Any, auth: dict[str, str], *, secret: bool = False, team: bool = False
) -> dict[str, Any]:
    response = await client.post(
        "/api/transfer/imports/inspect",
        headers=auth,
        files={"file": ("fixture.xfpack", sample(secret=secret, team=team))},
        data={"password": "passphrase" if secret else ""},
    )
    assert response.status_code == 200, response.text
    assert "temporary-key" not in response.text
    return dict(response.json())


async def plan(
    client: Any, auth: dict[str, str], identity: str, choices: dict[str, Any] | None = None
) -> str:
    response = await client.post(
        f"/api/transfer/imports/{identity}/plan", headers=auth, json={"choices": choices or {}}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["revision"])


async def test_team_import_maps_members_models_and_stays_stopped(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env
    inspection = await preview(client, auth, secret=True, team=True)
    identity = inspection["id"]
    revision = await plan(client, auth, identity)
    body = {"revision": revision, "password": "passphrase"}
    committed = await client.post(
        f"/api/transfer/imports/{identity}/commit", headers=auth, json=body
    )
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert len(result["created_agents"]) == 3
    mapping = result["mapping"]
    team = server.services.agent_repo.get(mapping["expert:team"])
    assert team.last_state == "stopped"
    assert team.enabled
    manager = server.app_runtime.agent_registry
    assert set(manager.teams.member_ids(team.agent_id)) == {
        mapping["expert:a"],
        mapping["expert:b"],
    }
    assert team.system_prompt == "先写作，再交叉审核"
    assert json.loads(team.config_json)["team_member_models"] == {
        mapping["expert:a"]: mapping["text:p"] + "/test/model"
    }
    for ref in ("expert:a", "expert:b"):
        row = server.services.agent_repo.get(mapping[ref])
        assert row.last_state == "stopped"
        assert row.default_model == mapping["text:p"] + "/test/model"
    provider = server.services.provider_repo.get_by_name(mapping["text:p"])
    assert provider.api_key == "temporary-key"
    repeated = await client.post(
        f"/api/transfer/imports/{identity}/commit", headers=auth, json=body
    )
    assert repeated.json() == result
    assert not (
        server.services.paths.root / "transfer" / "1" / identity / "package.xfpack"
    ).exists()


async def test_non_admin_cannot_read_other_jobs_or_create_providers(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, _server, auth = env
    inspection = await preview(client, auth)
    peer = await create_user(client, auth, username="transfer_peer")
    denied = await client.get(f"/api/transfer/jobs/{inspection['id']}", headers=peer)
    assert denied.status_code == 404
    inspection = await preview(client, peer)
    revision = await plan(client, peer, inspection["id"])
    denied = await client.post(
        f"/api/transfer/imports/{inspection['id']}/commit",
        headers=peer,
        json={"revision": revision},
    )
    assert denied.status_code == 403
    assert (await client.get("/api/transfer/inventory", headers=peer)).json()[
        "can_manage_models"
    ] is False


async def test_reuse_existing_provider_and_skip_preserve_target(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env
    provider = server.services.provider_repo.create(
        name="existing",
        kind="openai",
        api_key="existing-secret",
        models_json='[{"id":"test/model"}]',
    )
    inspection = await preview(client, auth)
    revision = await plan(
        client,
        auth,
        inspection["id"],
        {"text:p": {"action": "reuse", "target": str(provider)}, "expert:b": {"action": "skip"}},
    )
    response = await client.post(
        f"/api/transfer/imports/{inspection['id']}/commit",
        headers=auth,
        json={"revision": revision},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created_providers"] == 0
    assert len(response.json()["created_agents"]) == 1
    assert server.services.provider_repo.get(provider).api_key == "existing-secret"


async def test_failure_rolls_back_only_new_resources(
    env: tuple[Any, Any, dict[str, str]], monkeypatch: Any
) -> None:
    client, server, auth = env
    original = server.services.provider_repo.create(name="keep", kind="openai", api_key="preserve")
    inspection = await preview(client, auth)
    revision = await plan(client, auth, inspection["id"])
    monkeypatch.setattr(
        server.app_runtime.agent_registry, "create", AsyncMock(side_effect=RuntimeError("injected"))
    )
    with pytest.raises(RuntimeError, match="injected"):
        await client.post(
            f"/api/transfer/imports/{inspection['id']}/commit",
            headers=auth,
            json={"revision": revision},
        )
    assert [r.id for r in server.services.provider_repo.list_all()] == [original]
    job = (await client.get(f"/api/transfer/jobs/{inspection['id']}", headers=auth)).json()
    assert job["state"] == "failed"


async def test_cancel_removes_staged_file_and_invalidates_commit(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env
    inspection = await preview(client, auth)
    revision = await plan(client, auth, inspection["id"])
    assert (
        await client.delete(f"/api/transfer/imports/{inspection['id']}", headers=auth)
    ).status_code == 204
    response = await client.post(
        f"/api/transfer/imports/{inspection['id']}/commit",
        headers=auth,
        json={"revision": revision},
    )
    assert response.status_code == 400
    assert not server.services.provider_repo.list_all()


async def test_background_export_never_copies_login_or_memory(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env
    manager = server.app_runtime.agent_registry
    provider = server.services.provider_repo.create(
        name="source", kind="openai", api_key="never-copy", models_json='[{"id":"model"}]'
    )

    async def initialize(_row: Any, workspace: Any) -> None:
        await workspace.aupload_bytes("SOUL.md", "可分享的定义".encode())
        await workspace.aupload_bytes("USER.md", b"private-user")
        await workspace.aupload_bytes("MEMORY.md", b"private-memory")

    row = await manager.create(
        AgentCreateSpec(user_id=1, name="source", default_model="source/model"),
        start_runtime=False,
        workspace_initializer=initialize,
    )
    server.services.agent_repo.set_enabled(row.agent_id, True)
    response = await client.post(
        "/api/transfer/exports",
        headers=auth,
        json={"agent_ids": [row.agent_id], "include_skills": False},
    )
    assert response.status_code == 200, response.text
    identity = response.json()["id"]
    for _ in range(100):
        job = (await client.get(f"/api/transfer/jobs/{identity}", headers=auth)).json()
        if job["state"] in {"failed", "completed"}:
            break
        await asyncio.sleep(0.02)
    assert job["state"] == "completed", job
    assert any(item["path"] == "SOUL.md" for item in job["preview"])
    assert all("private" not in item["sample"] for item in job["preview"])
    content = (await client.get(f"/api/transfer/exports/{identity}/file", headers=auth)).content
    package, files, secrets = decode(content)
    assert secrets == {}
    assert all(b"private" not in blob for blob in files.values())
    assert all(not p.has_secret for p in package.providers)
    assert server.services.provider_repo.get(provider).api_key == "never-copy"


async def test_recovery_rolls_back_interrupted_disabled_expert(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env
    inspection = await preview(client, auth)
    service = server._portable_pack_service
    user = User(id=1, username="admin", role="admin", display_name=None)
    identity = inspection["id"]
    row = await service.manager.create(
        AgentCreateSpec(user_id=1, name="interrupted", config={"transfer_import": identity}),
        start_runtime=False,
    )
    state = service._read(user, identity)
    state.update(state="committing", agents=[row.agent_id], providers=[])
    service._save(user, identity, state)
    await service.recover(user)
    assert server.services.agent_repo.get(row.agent_id) is None
    assert service._read(user, identity)["state"] == "failed"


async def test_recovery_expires_old_terminal_staging_only(
    env: tuple[Any, Any, dict[str, str]],
) -> None:
    client, server, auth = env
    await preview(client, auth)
    service = server._portable_pack_service
    user = User(id=1, username="admin", role="admin", display_name=None)
    old_id = "a" * 32
    active_id = "b" * 32
    for identity, state in [(old_id, "completed"), (active_id, "awaiting_mapping")]:
        path = service._dir(user, identity) / "package.xfpack"
        service._write_file(path, b"staged")
        service._save(
            user,
            identity,
            {"kind": "import", "state": state, "created_at": 1},
        )
    await service.recover(user)
    assert not service._dir(user, old_id).exists()
    assert service._dir(user, active_id).exists()
    assert not server.services.settings_repo.get(service._key(user, old_id))
