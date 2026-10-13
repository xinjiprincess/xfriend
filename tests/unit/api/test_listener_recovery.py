"""Regression: Windows accept failure must not permanently kill the local API."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from starlette.responses import StreamingResponse

from octop.api.listener import ResilientServer


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Proactor listener regression")
@pytest.mark.asyncio
async def test_lost_listener_recovers_without_restarting_runtime_or_active_stream() -> None:
    starts = 0
    release = asyncio.Event()
    streaming = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        nonlocal starts
        starts += 1
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def content() -> Any:
            yield b"before\n"
            streaming.set()
            await release.wait()
            yield b"after\n"

        return StreamingResponse(content())

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    config.load()
    server = ResilientServer(config)
    server.lifespan = config.lifespan_class(config)
    await server.startup()
    listener = server.servers[0]
    port = listener.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False
        ) as client:
            task = asyncio.create_task(client.get("/stream"))
            await asyncio.wait_for(streaming.wait(), 3)
            # Exactly the failure state of CPython #93821: the Server still says
            # serving, but its underlying socket was closed by the accept loop.
            listener._sockets[0].close()
            assert listener.is_serving()
            await server.on_tick(10)
            response = await client.get("/health")
            assert response.json() == {"ok": True}
            assert server.servers[-1].sockets[0].getsockname()[1] == port
            assert starts == 1
            release.set()
            assert (await asyncio.wait_for(task, 3)).text == "before\nafter\n"
    finally:
        release.set()
        server.should_exit = True
        await server.shutdown()


@pytest.mark.asyncio
async def test_recovery_retries_bind_failure_but_does_not_reopen_during_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = uvicorn.Config(FastAPI(), log_level="error")
    config.load()
    server = ResilientServer(config)
    listener = MagicMock()
    listener.sockets = []
    server.servers = [listener]
    server._listener_bindings = [(listener, "127.0.0.1", 12345, 2)]
    replacement = MagicMock()
    replacement.sockets = [MagicMock()]
    replacement.sockets[0].fileno.return_value = 42
    create = AsyncMock(side_effect=[OSError("port occupied"), replacement])
    monkeypatch.setattr(asyncio.get_running_loop(), "create_server", create)
    await server.on_tick(10)
    assert server._listener_bindings[0][0] is listener
    await server.on_tick(20)
    assert server._listener_bindings[0][0] is replacement
    await server.on_tick(30)
    assert create.await_count == 2
    replacement.sockets = []
    server.should_exit = True
    assert await server.on_tick(40)
    assert create.await_count == 2
