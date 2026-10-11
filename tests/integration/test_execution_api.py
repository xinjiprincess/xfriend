"""Unified task API uses the real ACP adapter and persisted task projection."""

import asyncio
import sys
from pathlib import Path


async def test_execution_dsh_roundtrip(env_acp_agent, tmp_path):
    client, server, auth, agent_id = env_acp_agent
    runner = {
        "enabled": True,
        "command": sys.executable,
        "args": [str(Path(__file__).parents[1] / "support/fake_dsh.py")],
        "trusted": False,
    }
    configured = await client.put(f"/api/agents/{agent_id}/acp/dsh", headers=auth, json=runner)
    assert configured.status_code == 200
    bound = await client.put(
        f"/api/execution/experts/{agent_id}",
        headers=auth,
        json={"engine": "dsh", "workspace": str(tmp_path)},
    )
    assert bound.status_code == 200, bound.text
    preferences = await client.put(
        "/api/execution/assistant",
        headers=auth,
        json={"expert_ids": [agent_id], "wake_word": "hello"},
    )
    assert preferences.status_code == 200
    created = await client.post(
        "/api/execution/tasks", headers=auth, json={"expert_ids": [agent_id], "text": "hello"}
    )
    assert created.status_code == 201, created.text
    endpoint = "/api/execution/tasks/" + created.json()["id"]
    async with asyncio.timeout(20):
        while True:
            response = await client.get(endpoint, headers=auth)
            assert response.status_code == 200, response.text
            task = response.json()
            if task["state"] not in {"queued", "running", "permission"}:
                break
            await asyncio.sleep(0.05)
    assert task["state"] == "succeeded", task
    assert task["messages"][-1]["engine"] == "dsh"
    assert task["steps"][0]["engine_session_id"]
    preview = await client.get(endpoint + "/document-preview", headers=auth)
    assert preview.status_code == 200, preview.text
    exported = await client.post(
        endpoint + "/document",
        headers=auth,
        json={"format": "html", "mode": "all", "digest": preview.json()["digest"]},
    )
    assert exported.status_code == 200, exported.text
    assert "hello" in exported.text
    assert (await client.get(endpoint)).status_code == 401
    from octop.api.app import build_app

    schema = build_app(server).openapi()
    assert schema["paths"]["/api/execution/tasks"]["post"]["summary"]


async def test_native_execution_uses_gateway_history(env_fake_harness):
    from tests.support.auth import auth_header, bootstrap_admin, seed_openai_provider

    client, server, fake, home = env_fake_harness
    await bootstrap_admin(client, home)
    auth = await auth_header(client)
    await seed_openai_provider(client, auth)
    experts = (await client.get("/api/execution/experts", headers=auth)).json()
    assert experts
    created = await client.post(
        "/api/execution/tasks",
        headers=auth,
        json={"expert_ids": [experts[0]["id"]], "text": "hello"},
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    async with asyncio.timeout(15):
        while True:
            task = (await client.get("/api/execution/tasks/" + task_id, headers=auth)).json()
            if task["state"] not in {"queued", "running", "permission"}:
                break
            await asyncio.sleep(0.02)
    assert task["state"] == "succeeded", task
    assert task["messages"][-1]["content"] == "Hi Alice."
    assert task["steps"][0]["engine_session_id"]


async def test_default_local_shell_expert_can_bind_dsh(env_with_main_agent, tmp_path):
    client, server, auth, agent_id = env_with_main_agent
    response = await client.put(
        f"/api/execution/experts/{agent_id}",
        headers=auth,
        json={"engine": "dsh", "workspace": str(tmp_path)},
    )
    assert response.status_code == 200, response.text
