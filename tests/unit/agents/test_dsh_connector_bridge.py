from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from octop.infra.engines.dsh_connectors import DSHConnectorBridge
from octop.infra.errors import OctopError


async def test_connector_bridge_checks_selection_revoke_and_never_returns_credentials(monkeypatch):
    row = SimpleNamespace(instance_id="mail", display_name="Mail", status="active", kind="qq-mail")
    repo = SimpleNamespace(list_visible=lambda user_id: [row] if user_id == 1 else [])
    bridge = DSHConnectorBridge(
        SimpleNamespace(
            repos=SimpleNamespace(connector_repo=repo), config=SimpleNamespace(port=1234)
        )
    )
    svc = SimpleNamespace(
        ensure_fresh_credentials=AsyncMock(return_value={"password": "private"}),
        ensure_fresh_custom_servers=AsyncMock(),
        custom_harness_configs=Mock(return_value={}),
    )
    monkeypatch.setattr(bridge, "service", lambda: svc)
    monkeypatch.setattr(
        "octop.infra.engines.dsh_connectors.handle_mcp_request",
        lambda **kw: {
            "result": {
                "tools": [
                    {"name": "read", "description": "Read", "inputSchema": {"type": "object"}}
                ]
            }
            if kw["body"]["method"] == "tools/list"
            else {"content": [{"type": "text", "text": "ok"}]}
        },
    )
    grant = bridge.issue("one", 1)
    token = grant["url"].split("token=")[1]
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    empty = await bridge.handle("one", token, request, [])
    assert empty["result"]["tools"] == []
    reply = await bridge.handle("one", token, request, ["mail"])
    assert reply["result"]["tools"][0]["name"] == "mail__read"
    assert "private" not in str(reply)
    row.status = "disabled"
    with pytest.raises(OctopError):
        await bridge.handle(
            "one",
            token,
            {"id": 2, "method": "tools/call", "params": {"name": "mail__read"}},
            ["mail"],
        )
    bridge.revoke_user(1)
    with pytest.raises(OctopError):
        bridge.validate("one", token)


async def test_model_patch_contains_env_reference_not_key(tmp_path):
    row = SimpleNamespace(
        enabled=True, api_key="private-model-key", base_url="https://example.invalid/v1"
    )
    bridge = DSHConnectorBridge(
        SimpleNamespace(provider_repo=SimpleNamespace(get_by_name=lambda name: row))
    )
    env, args = await bridge.launch(1, tmp_path, {"model": '["provider", "model"]'})
    assert env["XFRIEND_MODEL_API_KEY"] == "private-model-key"
    assert args[0] == "--patch"
    assert "private-model-key" not in (tmp_path / "xfriend-model.patch.json").read_text()
    assert '"id": "acp"' in (tmp_path / "xfriend-model.patch.json").read_text()
