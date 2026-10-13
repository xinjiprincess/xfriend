from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from octop.api.middleware.startup import StartupMiddleware


async def test_startup_is_not_login_setup_or_activation_failure() -> None:
    called = []
    app = FastAPI()
    server = SimpleNamespace(_started=False)

    @app.get("/{path:path}")
    async def endpoint(path: str) -> dict[str, bool]:
        called.append(path)
        return {"ok": True}

    app.add_middleware(StartupMiddleware, server=server)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        for path in ("/api/setup/status", "/api/licensing/status", "/api/auth/me", "/api/agents"):
            response = await client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "SERVICE_STARTING"
            assert "setup_required" not in response.json()
            assert response.headers["retry-after"] == "1"
        assert not called
        assert (await client.get("/api/health")).status_code == 200
        assert (await client.get("/")).status_code == 200
        server._started = True
        assert (await client.get("/api/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_startup_websocket_returns_retryable_close() -> None:
    messages = []

    async def app(scope, receive, send):
        raise AssertionError("The incomplete runtime must not receive a connection")

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    await StartupMiddleware(app, SimpleNamespace(_started=False))(
        {"type": "websocket", "path": "/api/chat/ws"}, receive, send
    )
    assert messages == [{"type": "websocket.close", "code": 1013}]


async def test_core_routes_work_while_execution_returns_retryable_errors() -> None:
    app = FastAPI()
    server = SimpleNamespace(_started=True, runtime_ready=False)

    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
    async def endpoint(path: str) -> dict[str, str]:
        return {"path": path}

    app.add_middleware(StartupMiddleware, server=server)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        for path in (
            "/api/auth/me",
            "/api/setup/status",
            "/api/licensing/status",
            "/api/agents",
            "/api/execution/experts",
            "/api/settings/timezone",
            "/api/agents/expert/threads/thread/history",
            "/api/openapi.json",
        ):
            assert (await client.get(path)).status_code == 200
        for method, path in (
            ("POST", "/api/agents/expert/start"),
            ("POST", "/api/setup/finish"),
            ("DELETE", "/api/agents/expert"),
            ("GET", "/api/agents/expert/workspace"),
            ("POST", "/api/execution/tasks"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "SERVICE_STARTING"
            assert "setup_required" not in response.json()
        server.runtime_ready = True
        assert (await client.post("/api/agents/expert/start")).status_code == 200


async def test_optional_routes_loading_is_retryable_without_blocking_chat() -> None:
    app = FastAPI()
    pending = True

    @app.post("/api/agents/example/chat")
    async def chat() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        StartupMiddleware,
        server=SimpleNamespace(_started=True, runtime_ready=True),
        optional_routes_pending=lambda: pending,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.post("/api/agents/example/chat")).status_code == 200
        response = await client.get("/api/voice/settings")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_STARTING"
        pending = False
        assert (await client.get("/api/voice/settings")).status_code == 404
