"""Host plugin metadata gates compatibility and expert creation."""

import json
from pathlib import Path

import pytest

from octop.infra.agents.plugins.capabilities import load_capabilities


def test_incompatible_capability_manifest_rejected(tmp_path):
    manifest = {
        "id": "demo",
        "version": "1",
        "host": ">=99",
        "source": "local",
        "contributions": [{"type": "expert", "id": "planner", "name": "Planner"}],
    }
    (tmp_path / "xfriend.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="incompatible"):
        load_capabilities(tmp_path, "demo")
    manifest["host"] = ">=1,<2"
    (tmp_path / "xfriend.json").write_text(json.dumps(manifest))
    assert load_capabilities(tmp_path, "demo").contributions[0].engine == "native"


async def test_plugin_templates_create_independent_experts(env_acp_agent):
    client, server, auth, agent_id = env_acp_agent
    source = Path(__file__).parents[2] / "src/octop/infra/agents/plugins/bundled/xfriend-experts"
    server.plugin_manager.install_path(source)
    listed = await client.get("/api/execution/templates", headers=auth)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 3
    created = await client.post(
        "/api/execution/templates",
        headers=auth,
        json={"plugin_id": "xfriend-experts", "template_id": "planner", "name": "My planner"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["engine"] == "native"
    assert created.json()["id"] != agent_id
    row = server.services.agent_repo.get(created.json()["id"])
    assert "Singu Xfriend" in row.system_prompt
    assert json.loads(row.config_json)["host_template"]["version"] == "1.0.0"
