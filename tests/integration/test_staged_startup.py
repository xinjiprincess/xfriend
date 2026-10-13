"""Core HTTP/auth becomes usable while native experts are still restoring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

from octop.api.app import build_app, complete_deferred_routes
from octop.infra.agents.manager import AgentManager
from octop.infra.server import OctopServer
from tests.support.app import ensure_control_plane_bound, write_octop_config


async def test_core_auth_ready_before_experts(
    tmp_octop_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_octop_config(tmp_octop_home, require_setup_password=False)
    seed = OctopServer(home=tmp_octop_home)
    await seed.start()
    await ensure_control_plane_bound(seed)
    assert seed.services is not None and seed.user_manager is not None
    user = await seed.user_manager.create(
        username="startup-admin", password="TestStartup12!", role="admin"
    )
    seed.services.agent_repo.create(agent_id="slow", name="Slow expert", user_id=user.id)
    seed.services.agent_repo.set_state("slow", "running")
    await seed.stop()

    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_start(self: AgentManager, row: Any, **kwargs: Any) -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(AgentManager, "_start_agent", slow_start)
    server = OctopServer(home=tmp_octop_home)
    await server.start(defer_agents=True)
    try:
        assert server.startup_progress.core_ready
        assert not server.startup_progress.ready
        assert not entered.is_set()
        assert server.app_runtime is None
        assert server.database_bound and server.user_manager is not None
        assert server.thread_registry is not None
        app = build_app(server)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            health = (await client.get("/api/health")).json()
            assert health["core_ready"] and not health["ready"]
            assert health["users_loaded"] == 1
            server.start_background_services()
            await asyncio.wait_for(entered.wait(), 2)
            assert server.app_runtime is not None
            assert server.app_runtime.cron_manager._scheduler.state == STATE_PAUSED
            health = (await client.get("/api/health")).json()
            assert health["agents_pending"] == 1
            response = await client.post(
                "/api/auth/login",
                json={"username": "startup-admin", "password": "TestStartup12!"},
            )
            assert response.status_code == 200
            assert "setup_required" not in response.json()
            release.set()
            assert server._background_startup_task is not None
            await asyncio.wait_for(server._background_startup_task, 5)
            health = (await client.get("/api/health")).json()
            assert health["ready"] and health["agents_ready"]
            assert health["agents_pending"] == 0
            assert server.app_runtime.cron_manager._scheduler.state == STATE_RUNNING
            assert server.app_runtime.bridge_manager._asgi_app is app
    finally:
        release.set()
        await server.stop()


async def test_first_run_database_bind_starts_deferred_runtime(tmp_octop_home: Path) -> None:
    write_octop_config(tmp_octop_home, require_setup_password=False)
    server = OctopServer(home=tmp_octop_home)
    await server.start(defer_agents=True)
    try:
        assert server.startup_progress.ready  # Wizard has no runtime to restore yet.
        assert server.app_runtime is None
        await ensure_control_plane_bound(server)
        assert server._background_startup_task is not None
        await asyncio.wait_for(server._background_startup_task, 5)
        assert server.startup_progress.ready
        assert server.app_runtime is not None
        assert server.app_runtime.cron_manager._scheduler.state == STATE_RUNNING
    finally:
        await server.stop()


async def test_desktop_routes_complete_before_runtime_and_spa(tmp_octop_home: Path) -> None:
    write_octop_config(tmp_octop_home, require_setup_password=False)
    server = OctopServer(home=tmp_octop_home)
    await server.start(defer_agents=True)
    try:
        complete = build_app(server)
        staged = build_app(server, defer_optional_routes=True)
        assert staged.state.pending_router_specs
        initial_paths = staged.openapi()["paths"]
        assert "/api/auth/login" in initial_paths
        assert "/api/health" in initial_paths
        await complete_deferred_routes(staged)
        assert not staged.state.pending_router_specs
        assert staged.openapi()["paths"].keys() == complete.openapi()["paths"].keys()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=staged), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/setup/status")
            assert response.status_code == 200
        assert getattr(staged.routes[-1], "path", "") == getattr(complete.routes[-1], "path", "")
        count = len(staged.routes)
        await complete_deferred_routes(staged)
        assert len(staged.routes) == count
    finally:
        await server.stop()
