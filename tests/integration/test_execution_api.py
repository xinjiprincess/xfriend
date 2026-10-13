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


async def test_dual_api_runs_both_engines_and_final_summary(env_fake_harness, tmp_path):
    from tests.support.auth import auth_header, bootstrap_admin, seed_openai_provider

    client, server, fake, home = env_fake_harness
    await bootstrap_admin(client, home)
    auth = await auth_header(client)
    await seed_openai_provider(client, auth)
    experts = (await client.get("/api/execution/experts", headers=auth)).json()
    expert_id = experts[0]["id"]
    configured = await client.put(
        "/api/acp/dsh",
        headers=auth,
        json={
            "enabled": True,
            "command": sys.executable,
            "args": [str(Path(__file__).parents[1] / "support/fake_dsh.py")],
            "trusted": False,
        },
    )
    assert configured.status_code == 200, configured.text
    created = await client.post(
        "/api/execution/dual-tasks",
        headers=auth,
        json={
            "native_experts": [expert_id],
            "dsh_experts": [expert_id],
            "workspace": str(tmp_path),
            "summary_expert": expert_id,
            "summary_engine": "native",
            "text": "Prepare a final report",
        },
    )
    assert created.status_code == 201, created.text
    async with asyncio.timeout(35):
        while True:
            task = (
                await client.get("/api/execution/tasks/" + created.json()["id"], headers=auth)
            ).json()
            if task["state"] not in {"queued", "running", "permission"}:
                break
            await asyncio.sleep(0.05)
    assert task["state"] == "succeeded", task
    assert task["mode"] == "dual"
    assert [step["engine"] for step in task["steps"]] == ["native", "dsh", "native"]
    assert task["steps"][-1]["phase"] == "summary"
    assert task["messages"][-1]["phase"] == "summary"


async def test_dsh_mcp_configuration_requires_admin_and_valid_schema(env_acp_agent):
    client, server, auth, agent_id = env_acp_agent
    assert (await client.get("/api/engines/dsh/extensions")).status_code == 401
    bad = await client.put(
        "/api/engines/dsh/extensions",
        headers=auth,
        json={"extensions": [{"name": "bad", "transport": "http", "url": "file:///etc"}]},
    )
    assert bad.status_code == 422
    good = await client.put(
        "/api/engines/dsh/extensions",
        headers=auth,
        json={"extensions": [{"name": "tool", "command": sys.executable, "args": []}]},
    )
    assert good.status_code == 200, good.text
    read = await client.get("/api/engines/dsh/extensions", headers=auth)
    assert read.json() == good.json()
