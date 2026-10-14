"""Exercise the DSH HTTP surface, permission boundary and section exports."""

import asyncio
import sys
from pathlib import Path

from tests.support.auth import create_user


async def test_dsh_session_prompt_export_and_auth(env_acp_agent, tmp_path: Path) -> None:
    client, _server, auth, agent_id = env_acp_agent
    assert (await client.get("/api/health/startup")).status_code == 401
    startup = await client.get("/api/health/startup", headers=auth)
    assert startup.status_code == 200
    assert startup.json()["ready"]
    health = await client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["application"] == "xfriend"
    assert (await client.get("/api/engines/dsh/sessions")).status_code == 401
    runner = {
        "enabled": True,
        "command": sys.executable,
        "args": [str(Path(__file__).parents[1] / "support" / "fake_dsh.py")],
        "trusted": False,
    }
    configured = await client.put(f"/api/agents/{agent_id}/acp/dsh", headers=auth, json=runner)
    assert configured.status_code == 200
    created = await client.post(
        "/api/engines/dsh/sessions", headers=auth, json={"cwd": str(tmp_path)}
    )
    assert created.status_code == 200
    base = "/api/engines/dsh/sessions/" + created.json()["id"]
    sent = await client.post(base + "/prompt", headers=auth, json={"text": "hello"})
    assert sent.status_code == 200
    async with asyncio.timeout(15):
        while True:
            state = (await client.get(base, headers=auth)).json()
            if state["state"] not in {"running", "permission"}:
                break
            await asyncio.sleep(0.05)
    assert state["state"] == "idle", state
    preview = await client.get(base + "/document-preview", headers=auth)
    assert preview.status_code == 200
    selection = {"format": "html", "mode": "all", "digest": preview.json()["digest"]}
    result = await client.post(base + "/document", headers=auth, json=selection)
    assert result.status_code == 200
    assert "hello" in result.text
    selection["digest"] = "stale"
    assert (await client.post(base + "/document", headers=auth, json=selection)).status_code == 409
    assert (
        await client.get("/api/engines/dsh/sessions/not-a-uuid", headers=auth)
    ).status_code == 404


async def test_regular_user_builtin_expert_and_session_isolation(
    env_acp_agent, tmp_path: Path
) -> None:
    client, server, admin, _agent_id = env_acp_agent
    user = await create_user(client, admin, username="dsh-user")
    response = await client.get("/api/engines/dsh/expert", headers=user)
    assert response.status_code == 200
    assert response.json()["id"] == "dsh-expert"
    saved = await client.put(
        "/api/engines/dsh/expert", headers=user, json={"cwd": str(tmp_path), "connectors": []}
    )
    assert saved.status_code == 200
    created = await client.post("/api/engines/dsh/sessions", headers=user, json={"cwd": ""})
    assert created.status_code == 200
    identity = created.json()["id"]
    assert created.json()["cwd"] == str(tmp_path)
    assert (
        await client.get(f"/api/engines/dsh/sessions/{identity}", headers=admin)
    ).status_code == 404
    assert (await client.get("/api/engines/dsh/packages", headers=user)).status_code == 403
    assert (
        await client.put("/api/engines/dsh/extensions", headers=user, json={"extensions": []})
    ).status_code == 403
    assert (
        await client.put(
            "/api/engines/dsh/expert", headers=user, json={"connectors": ["other-user"]}
        )
    ).status_code == 403
    user_id = next(s.user_id for s in server.dsh_service.sessions.values() if s.id == identity)
    grant = server.dsh_service.bridge.issue(identity, user_id)
    token = grant["url"].split("token=")[1]
    assert server.dsh_service.bridge.validate(identity, token) == user_id
    await client.post("/api/auth/logout", headers=user)
    assert identity not in server.dsh_service.bridge.grants
