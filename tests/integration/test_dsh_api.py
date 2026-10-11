"""Exercise the DSH HTTP surface, permission boundary and section exports."""

import asyncio
import sys
from pathlib import Path


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
