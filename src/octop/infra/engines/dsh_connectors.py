"""Session-scoped MCP access to the user's currently authorized connectors."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any

from octop.infra.connectors.builder import build_http_mcp_spec
from octop.infra.connectors.catalog import get_catalog_entry, is_inprocess_gateway
from octop.infra.connectors.gateway.protocol import handle_mcp_request
from octop.infra.connectors.service import ConnectorService
from octop.infra.errors import ErrorCode, OctopError


class DSHConnectorBridge:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.grants: dict[str, tuple[str, int, float]] = {}

    def service(self) -> ConnectorService:
        return ConnectorService(
            repo=self.services.repos.connector_repo,
            secret_repo=self.services.secret_repo,
            settings_repo=self.services.settings_repo,
            config=self.services.config,
        )

    def issue(self, session_id: str, user_id: int) -> dict[str, Any]:
        previous = self.grants.get(session_id)
        token = previous[0] if previous and previous[1] == user_id else secrets.token_urlsafe(32)
        self.grants[session_id] = (token, user_id, time.monotonic() + 3600)
        port = self.services.config.port
        return {
            "name": "xfriend-connectors",
            "type": "http",
            "url": f"http://127.0.0.1:{port}/api/internal/mcp/dsh/{session_id}?token={token}",
            "headers": [],
        }

    def validate(self, session_id: str, token: str) -> int:
        grant = self.grants.get(session_id)
        if (
            grant is None
            or grant[2] <= time.monotonic()
            or not secrets.compare_digest(grant[0], token)
        ):
            raise OctopError(ErrorCode.AUTH_FAILED, "AUTH_FAILED")
        return grant[1]

    async def choices(self, user_id: int) -> list[dict[str, str]]:
        rows = await asyncio.to_thread(self.services.repos.connector_repo.list_visible, user_id)
        choices = [
            {"id": r.instance_id, "name": r.display_name, "status": r.status}
            for r in rows
            if get_catalog_entry(r.kind) is not None
        ]
        custom = await asyncio.to_thread(self.service().custom_harness_configs, user_id)
        choices.extend(
            {"id": "custom:" + name, "name": name, "status": "active"} for name in custom
        )
        return choices

    def revoke_user(self, user_id: int) -> None:
        self.grants = {key: value for key, value in self.grants.items() if value[1] != user_id}

    async def models(self) -> list[dict[str, str]]:
        rows = await asyncio.to_thread(self.services.provider_repo.list_all)
        return [
            {
                "id": json.dumps([row.name, str(model["id"])]),
                "name": row.name + " / " + str(model["id"]),
            }
            for row in rows
            if row.enabled
            and row.kind
            in {"openai", "deepseek", "openai_compatible", "custom", "openai-compatible"}
            for model in row.get_models()
            if model.get("id") and model.get("enabled", True)
        ]

    async def launch(
        self, user_id: int, home: Path, preferences: dict[str, Any], web_id: str = ""
    ) -> tuple[dict[str, str], list[str]]:
        patch: list[dict[str, Any]] = []
        env: dict[str, str] = {}
        if preferences.get("model"):
            provider, model = json.loads(preferences["model"])
            row = await asyncio.to_thread(self.services.provider_repo.get_by_name, provider)
            if row is None or not row.enabled:
                raise OctopError(ErrorCode.SLASH_BAD_ARGS, "SLASH_BAD_ARGS")
            env["XFRIEND_MODEL_API_KEY"] = row.api_key or ""
            route = {
                "api": "openai-completions",
                "apiKeyEnv": "XFRIEND_MODEL_API_KEY",
                "baseURL": row.base_url or "https://api.openai.com/v1",
                "models": [{"id": model, "contextWindow": 128000, "maxTokens": 8192}],
            }
            patch.extend(
                [
                    {"id": "llm-pi-ai", "config": {"providers": {"xfriend": route}}},
                    {
                        "id": "agent-default-model",
                        "config": {"provider": "xfriend", "model": model},
                    },
                ]
            )
            if not web_id:
                patch.append({"id": "acp", "config": {"provider": "xfriend", "model": model}})
        if web_id and preferences.get("connectors"):
            grant = self.issue(web_id, user_id)
            patch.append(
                {
                    "insert": [
                        {
                            "id": "xfriend-connectors",
                            "name": "@deepseek-ai/dsh-mcp-client",
                            "config": {
                                "transport": "streamable-http",
                                "serverName": "xfriend-connectors",
                                "url": grant["url"],
                                "headers": {},
                                "failOnStartupError": False,
                            },
                        }
                    ]
                }
            )
        if not patch:
            return env, []
        path = home / ("xfriend-web.patch.json" if web_id else "xfriend-model.patch.json")
        await asyncio.to_thread(path.write_text, json.dumps(patch), encoding="utf-8")
        return env, ["--patch", str(path)]

    @staticmethod
    async def remote(spec: dict[str, Any], method: Any, params: dict[str, Any]) -> dict[str, Any]:
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        if (
            method == "tools/call"
            and spec.get("allowed_tools") is not None
            and params["name"] not in spec["allowed_tools"]
        ):
            raise OctopError(ErrorCode.FORBIDDEN, "FORBIDDEN")
        async with AsyncExitStack() as stack:
            if spec.get("transport") == "stdio":
                read, write = await stack.enter_async_context(
                    stdio_client(
                        StdioServerParameters(
                            command=spec["command"], args=spec.get("args", []), env=spec.get("env")
                        )
                    )
                )
            else:
                read, write, _ = await stack.enter_async_context(
                    streamablehttp_client(spec["url"], headers=spec.get("headers"))
                )
            client = await stack.enter_async_context(ClientSession(read, write))
            await client.initialize()
            response = (
                await client.list_tools()
                if method == "tools/list"
                else await client.call_tool(params["name"], params.get("arguments", {}))
            )
            result = dict(response.model_dump(by_alias=True, exclude_none=True))
            if method == "tools/list" and spec.get("allowed_tools") is not None:
                result["tools"] = [
                    tool for tool in result["tools"] if tool["name"] in spec["allowed_tools"]
                ]
            return result

    async def handle(
        self, session_id: str, token: str, body: dict[str, Any], selected: list[str]
    ) -> dict[str, Any]:
        user_id = self.validate(session_id, token)
        method, request_id = body.get("method"), body.get("id")
        if method == "initialize":
            result: Any = {
                "protocolVersion": (body.get("params") or {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xfriend-connectors", "version": "1"},
            }
        elif method == "ping":
            result = {}
        elif method == "notifications/initialized":
            return {}
        elif method in {"tools/list", "tools/call"}:
            svc = self.service()
            rows = await asyncio.to_thread(self.services.repos.connector_repo.list_visible, user_id)
            visible = {
                r.instance_id: r for r in rows if r.status == "active" and r.instance_id in selected
            }
            tools: list[dict[str, Any]] = []
            params = body.get("params") or {}
            requested = str(params.get("name", ""))
            custom: dict[str, Any] = {}
            if any(item.startswith("custom:") for item in selected):
                await svc.ensure_fresh_custom_servers(user_id)
                custom = await asyncio.to_thread(svc.custom_harness_configs, user_id)
            for name, spec in custom.items():
                if "custom:" + name not in selected:
                    continue
                prefix = "custom_" + name + "__"
                if method == "tools/call":
                    if requested.startswith(prefix):
                        result = await self.remote(
                            spec, method, {**params, "name": requested.removeprefix(prefix)}
                        )
                        return {"jsonrpc": "2.0", "id": request_id, "result": result}
                    continue
                current_custom = await self.remote(spec, method, {})
                tools.extend(
                    {**tool, "name": prefix + tool["name"]}
                    for tool in current_custom.get("tools", [])
                )
            for identity, row in visible.items():
                entry = get_catalog_entry(row.kind)
                if entry is None:
                    continue
                prefix = identity + "__"
                if method == "tools/call" and not requested.startswith(prefix):
                    continue
                creds = await svc.ensure_fresh_credentials(identity, row.kind)
                message = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": {
                        "name": requested.removeprefix(prefix),
                        "arguments": params.get("arguments", {}),
                    },
                }
                if (
                    method == "tools/call"
                    and entry.allowed_tools is not None
                    and message["params"]["name"] not in entry.allowed_tools
                ):
                    raise OctopError(ErrorCode.FORBIDDEN, "FORBIDDEN")
                if is_inprocess_gateway(entry):
                    reply = await asyncio.to_thread(
                        handle_mcp_request, kind=row.kind, creds=creds, body=message
                    )
                    current = reply.get("result", {})
                else:
                    spec = build_http_mcp_spec(
                        entry=entry, instance_id=identity, creds=creds, config=self.services.config
                    )
                    current = await self.remote(spec, method, message["params"])
                if method == "tools/call":
                    result = current
                    break
                for tool in current.get("tools", []):
                    if entry.allowed_tools is None or tool["name"] in entry.allowed_tools:
                        tools.append({**tool, "name": prefix + tool["name"]})
            else:
                if method == "tools/call":
                    raise OctopError(ErrorCode.FORBIDDEN, "FORBIDDEN")
                result = {"tools": tools}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
