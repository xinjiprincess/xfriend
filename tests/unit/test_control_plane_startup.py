"""Engine SDK imports and workers must not own the control-plane lifecycle."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer


def _prepare_home(home: Path) -> None:
    (home / "config.json").write_text(
        json.dumps({"require_setup_password": False, "history_v2_enabled": True}), "utf-8"
    )
    (home / "xfriend.db").touch()


def test_core_and_plugin_metadata_never_import_execution_sdk(tmp_path: Path) -> None:
    _prepare_home(tmp_path)
    plugin = tmp_path / "plugins" / "slow-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        "id: slow-plugin\nversion: 1.0\nname: Slow plugin\nentry: main.py\n", "utf-8"
    )
    (plugin / "main.py").write_text("raise AssertionError('Plugin code executed')", "utf-8")
    script = """
import asyncio, importlib.abc, sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'octop_harness', 'harness', 'deepagents', 'langgraph', 'openai', 'google'}:
            raise AssertionError('Control plane imported execution SDK: ' + fullname)
sys.meta_path.insert(0, Guard())
from octop.infra.server import OctopServer
async def main():
    server = OctopServer(Path(sys.argv[1]))
    try:
        await server.start(defer_agents=True)
        assert server.database_bound and server.user_manager is not None
        assert server.history_archive is not None and server.thread_registry is not None
        assert server.startup_progress.core_ready and not server.runtime_ready
        entries = server.plugin_manager.list_installed()
        assert entries[0]['id'] == 'slow-plugin' and entries[0]['loaded'] is False
        assert server.plugin_manager.contributions() == []
        await server.user_manager.create(username='old-admin', password='OldAdminPass12!', role='admin')
    finally:
        await server.stop()
    server = OctopServer(Path(sys.argv[1]))
    try:
        await server.start(defer_agents=True)
        assert server.database_bound and server.user_manager.count() == 1
        assert server.app_runtime is None
    finally:
        await server.stop()
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def test_engine_import_failure_leaves_accounts_and_history_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_home(tmp_path)

    def fail_imports() -> None:
        raise ImportError("Broken optional engine")

    monkeypatch.setattr("octop.infra.server._preload_runtime_modules", fail_imports)
    server = OctopServer(tmp_path)
    await server.start(defer_agents=True)
    try:
        server.start_background_services()
        assert server._background_startup_task is not None
        await server._background_startup_task
        assert server.startup_progress.core_ready and server.database_bound
        assert server.startup_progress.runtime_failed and not server.runtime_ready
        assert server.user_manager is not None and server.history_archive is not None
        assert server.user_manager.count() == 0
        with pytest.raises(OctopError) as raised:
            await server.ensure_runtime_ready()
        assert raised.value.code == ErrorCode.SERVICE_STARTING
    finally:
        await server.stop()
    assert server.services is None and server.history_archive is None


async def test_stop_drains_import_worker_and_never_assembles_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_home(tmp_path)
    entered, release = threading.Event(), threading.Event()
    assembled = False

    def slow_imports() -> None:
        entered.set()
        assert release.wait(10)

    async def boot_runtime(self: OctopServer, config: Any) -> None:
        nonlocal assembled
        assembled = True

    monkeypatch.setattr("octop.infra.server._preload_runtime_modules", slow_imports)
    monkeypatch.setattr(OctopServer, "_boot_runtime", boot_runtime)
    server = OctopServer(tmp_path)
    await server.start(defer_agents=True)
    stopping: asyncio.Task[None] | None = None
    try:
        server.start_background_services()
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        stopping = asyncio.create_task(server.stop())
        await asyncio.sleep(0.02)
        assert not stopping.done()  # A cancelled Future does not kill its worker.
        assert server._background_startup_task is not None
        server._background_startup_task.cancel()  # Repeated shutdown must also drain.
        await asyncio.sleep(0.01)
        assert not stopping.done()
        assert server.services is not None
        release.set()
        await asyncio.wait_for(stopping, 5)
        assert not assembled and server.services is None and not server.runtime_ready
    finally:
        release.set()
        if stopping is not None:
            await stopping
        await server.stop()


async def test_cancelled_waiter_does_not_cancel_shared_engine_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare_home(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def prepare(self: OctopServer, config: Any) -> None:
        entered.set()
        await release.wait()
        raise ImportError("End the fake runtime after verifying cancellation")

    monkeypatch.setattr(OctopServer, "_prepare_runtime", prepare)
    server = OctopServer(tmp_path)
    await server.start(defer_agents=True)
    try:
        waiter = asyncio.create_task(server.ensure_runtime_ready())
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert server._background_startup_task is not None
        assert not server._background_startup_task.done()
        release.set()
        await server._background_startup_task
        assert server.startup_progress.runtime_failed and server.database_bound
    finally:
        release.set()
        await server.stop()
