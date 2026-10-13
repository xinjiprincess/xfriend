"""Launch composition root must tolerate deferred control-plane bind."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.server import OctopServer
from octop.launch import _warmup_after_listen


@pytest.mark.asyncio
async def test_deferred_start_exposes_config_for_listen(tmp_octop_home: Path) -> None:
    """``octop run`` reads bind host/port from config before the DB is bound."""
    srv = OctopServer(home=tmp_octop_home)
    await srv.start()
    try:
        assert srv.services is None
        cfg = srv.services.config if srv.services is not None else srv.config
        assert cfg is not None
        assert isinstance(cfg.port, int)
        assert cfg.bind_host
    finally:
        await srv.stop()


async def test_desktop_restores_experts_before_optional_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    order: list[str] = []
    srv = SimpleNamespace(_background_startup_task=None)

    async def restore() -> None:
        order.append("restore")
        entered.set()
        await release.wait()
        order.append("restored")

    def start() -> None:
        srv._background_startup_task = asyncio.create_task(restore())

    async def complete(app: object) -> None:
        order.append("optional")

    srv.start_background_services = start
    monkeypatch.setattr("octop.api.app.complete_deferred_routes", complete)
    app = SimpleNamespace(state=SimpleNamespace(pending_router_specs=[object()]))
    task = asyncio.create_task(_warmup_after_listen([SimpleNamespace(started=True)], srv, app))
    await asyncio.wait_for(entered.wait(), 2)
    assert order == ["restore"]
    release.set()
    await asyncio.wait_for(task, 2)
    assert order == ["restore", "restored", "optional"]
