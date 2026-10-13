"""Startup concurrency, request cancellation and explicitly stopped experts."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from octop_harness.registry import AgentEntry

from octop.config import OctopConfig
from octop.infra.agents.manager import AgentManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout


@pytest.fixture
async def manager(tmp_path: Path) -> AsyncIterator[AgentManager]:
    paths = PathLayout(tmp_path)
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    value = AgentManager(repos=services.repos, paths=paths)
    try:
        yield value
    finally:
        await value.shutdown()
        db.close()


async def test_bounded_restore_and_cancelled_waiter(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(7):
        manager._repos.agent_repo.create(agent_id=f"a{index}", user_id=None, name=f"Expert {index}")
    release, saturated = asyncio.Event(), asyncio.Event()
    active = 0
    maximum = 0
    started: list[str] = []

    async def start(row: Any, **kwargs: Any) -> None:
        nonlocal active, maximum
        started.append(row.agent_id)
        active += 1
        maximum = max(maximum, active)
        if active == 3:
            saturated.set()
        try:
            await release.wait()
        finally:
            active -= 1

    monkeypatch.setattr(manager, "_start_agent", start)
    await manager.boot(restore_agents=False)
    assert not started
    manager.begin_agent_restore()
    await asyncio.wait_for(saturated.wait(), 2)
    assert manager.agents_pending == 7
    waiter = asyncio.create_task(manager.wait_for_agent_startup("a0"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not manager._startup_tasks["a0"].cancelled()
    release.set()
    await asyncio.wait_for(manager.wait_for_agent_restore(), 2)
    assert maximum == 3
    assert sorted(started) == [f"a{index}" for index in range(7)]
    assert manager.agents_pending == 0
    manager.begin_agent_restore()
    assert len(started) == 7


async def test_default_assistant_restores_before_older_experts(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(3):
        manager._repos.agent_repo.create(
            agent_id=f"older-{index}", user_id=None, name=f"Old {index}"
        )
    manager._repos.agent_repo.create(
        agent_id="assistant",
        user_id=None,
        name="Xfriend Assistant",
        template_name="general-assistant",
    )
    started: list[str] = []

    async def start(row: Any, **kwargs: Any) -> None:
        started.append(row.agent_id)

    monkeypatch.setattr(manager, "_start_agent", start)
    await manager.boot(restore_agents=False)
    manager.begin_agent_restore()
    await manager.wait_for_agent_restore()
    assert started[0] == "assistant"


async def test_recent_conversation_restores_before_assistant(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._repos.agent_repo.create(agent_id="older", user_id=None, name="Old")
    manager._repos.agent_repo.create(
        agent_id="assistant", user_id=None, name="Assistant", template_name="general-assistant"
    )
    manager._repos.agent_repo.create(agent_id="recent", user_id=None, name="Recent")
    user_id = manager._repos.user_repo.create(
        username="recent-user", password_hash="test", role="user"
    )
    manager._repos.session_repo.upsert(
        session_key="recent-session",
        agent_id="recent",
        user_id=user_id,
        channel_type="dashboard",
        chat_type="dm",
        thread_id="recent-thread",
    )
    started: list[str] = []

    async def start(row: Any, **kwargs: Any) -> None:
        started.append(row.agent_id)

    monkeypatch.setattr(manager, "_start_agent", start)
    await manager.boot(restore_agents=False)
    manager.begin_agent_restore()
    await manager.wait_for_agent_restore()
    assert started == ["recent", "assistant", "older"]


async def test_stopping_queued_expert_prevents_start(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(4):
        manager._repos.agent_repo.create(agent_id=f"a{index}", user_id=None, name=f"Expert {index}")
    release, entered = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def start(row: Any, **kwargs: Any) -> None:
        calls.append(row.agent_id)
        if len(calls) == 3:
            entered.set()
        await release.wait()

    monkeypatch.setattr(manager, "_start_agent", start)
    await manager.boot(restore_agents=False)
    manager.begin_agent_restore()
    await asyncio.wait_for(entered.wait(), 2)
    await manager.stop("a3")
    release.set()
    await asyncio.wait_for(manager.wait_for_agent_restore(), 2)
    assert "a3" not in calls
    with pytest.raises(OctopError) as error:
        await manager.ensure_agent_runtime("a3")
    assert error.value.code == ErrorCode.AGENT_NOT_RUNNING


async def test_team_waits_for_its_pending_members(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._repos.agent_repo.create(agent_id="team", user_id=None, name="Team", kind="team")
    manager._repos.agent_repo.create(agent_id="member", user_id=None, name="Member")
    release = asyncio.Event()

    async def start(row: Any, **kwargs: Any) -> None:
        if row.agent_id == "member":
            await release.wait()

    monkeypatch.setattr(manager, "_start_agent", start)
    monkeypatch.setattr(manager, "get_agent", lambda aid: SimpleNamespace(agent_id=aid))
    monkeypatch.setattr(manager._teams, "member_ids", lambda aid: ["member"])
    await manager.boot(restore_agents=False)
    result = asyncio.create_task(manager.ensure_agent_runtime("team"))
    await asyncio.sleep(0.02)
    assert not result.done()
    release.set()
    assert (await asyncio.wait_for(result, 2)).agent_id == "team"


async def test_conversation_waits_for_configuration_reload(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = SimpleNamespace(agent_id="reloading")
    monkeypatch.setattr(manager, "wait_for_agent_startup", AsyncMock())
    get_agent = AsyncMock()
    monkeypatch.setattr(manager, "get_agent", lambda aid: get_agent.return_value)
    lock = manager._lifecycle_lock_for("reloading")
    async with lock:
        result = asyncio.create_task(manager.ensure_agent_runtime("reloading"))
        await asyncio.sleep(0)
        assert not result.done()
        get_agent.return_value = runtime
    assert await asyncio.wait_for(result, 2) is runtime


async def test_shutdown_cancels_all_pending_restores(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._repos.agent_repo.create(agent_id="slow", user_id=None, name="Slow")
    entered, finished = asyncio.Event(), asyncio.Event()

    async def start(row: Any, **kwargs: Any) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    monkeypatch.setattr(manager, "_start_agent", start)
    await manager.boot(restore_agents=False)
    manager.begin_agent_restore()
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(manager.shutdown(), 2)
    assert finished.is_set()
    assert not manager._startup_tasks


async def test_cancelled_restore_is_not_restarted_by_a_late_request(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._repos.agent_repo.create(agent_id="slow", user_id=None, name="Slow")
    entered = asyncio.Event()
    calls = 0

    async def start(row: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_start_agent", start)
    await manager.boot(restore_agents=False)
    manager.begin_agent_restore()
    await asyncio.wait_for(entered.wait(), 2)
    await manager.cancel_agent_restore()
    with pytest.raises(OctopError) as error:
        await asyncio.wait_for(manager.ensure_agent_runtime("slow"), 2)
    assert error.value.code == ErrorCode.AGENT_NOT_RUNNING
    assert calls == 1
    assert not manager._startup_tasks


def _blocked_thread_creation(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> tuple[asyncio.Event, threading.Event, Any, Path]:
    """Keep the real harness acreate_agent -> to_thread boundary in these tests."""
    harness = manager.harness_manager
    assert harness is not None
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    workspace = manager.paths.agents_dir / "slow"
    agent = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(harness, "_shared_factory", SimpleNamespace(aclose=AsyncMock()))
    monkeypatch.setattr(manager, "_agent_runtime_bundle", lambda row: (None, {}, [], "User"))
    monkeypatch.setattr(manager, "_post_start_agent", AsyncMock())
    monkeypatch.setattr(manager, "resolve_workspace_dir", lambda aid, **kwargs: workspace)

    def create(config: Any, *, agent_id: str, **kwargs: Any) -> AgentEntry:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(15), "test did not release the graph construction worker"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "worker-created.txt").write_text("created", encoding="utf-8")
        entry = AgentEntry(
            agent_id=agent_id,
            agent=agent,
            config=config,
            metadata={},
            tags=[],
            created_at=datetime.now(UTC),
        )
        harness._registry.add(entry)
        return entry

    monkeypatch.setattr(harness, "create_agent", create)
    return entered, release, agent, workspace


async def test_shutdown_drains_worker_before_releasing_lifecycle_lock(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._repos.agent_repo.create(agent_id="slow", user_id=None, name="Slow")
    await manager.boot(restore_agents=False)
    harness = manager.harness_manager
    entered, release, agent, _ = _blocked_thread_creation(manager, monkeypatch)
    draining = asyncio.Event()
    original_drain = manager._drain_cancelled_agent_creation

    async def drain(agent_id: str, creation: Any) -> None:
        draining.set()
        await original_drain(agent_id, creation)

    monkeypatch.setattr(manager, "_drain_cancelled_agent_creation", drain)
    manager.begin_agent_restore()
    shutdown = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        shutdown = asyncio.create_task(manager.shutdown())
        await asyncio.wait_for(draining.wait(), 3)
        assert not shutdown.done()
        assert manager._lifecycle_lock_for("slow").locked()
        manager._startup_tasks["slow"].cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert manager._lifecycle_lock_for("slow").locked()
    finally:
        release.set()
        if shutdown is not None:
            await asyncio.wait_for(shutdown, 5)
    assert harness is not None and not harness._registry.agent_ids()
    agent.aclose.assert_awaited_once()
    assert manager.harness_manager is None
    assert manager._repos.agent_repo.get("slow") is not None


async def test_delete_waits_for_worker_then_removes_runtime_workspace_and_row(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._repos.agent_repo.create(agent_id="slow", user_id=None, name="Slow")
    await manager.boot(restore_agents=False)
    entered, release, agent, workspace = _blocked_thread_creation(manager, monkeypatch)
    manager.begin_agent_restore()
    deletion = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        deletion = asyncio.create_task(manager.delete("slow"))
        await asyncio.sleep(0)
        assert not deletion.done()
        assert manager._repos.agent_repo.get("slow") is not None
    finally:
        release.set()
        if deletion is not None:
            await asyncio.wait_for(deletion, 5)
        await asyncio.wait_for(manager.wait_for_agent_restore(), 5)
    assert manager._octop_harness_or_none("slow") is None
    assert manager._repos.agent_repo.get("slow") is None
    assert not workspace.exists()
    agent.aclose.assert_awaited_once()


async def test_delete_queued_restore_does_not_recreate_agent(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager._repos.agent_repo.create(agent_id="slow", user_id=None, name="Slow")
    await manager.boot(restore_agents=False)
    _, release, agent, workspace = _blocked_thread_creation(manager, monkeypatch)
    release.set()
    manager._startup_limit = asyncio.Semaphore(0)
    manager.begin_agent_restore()
    await asyncio.sleep(0)
    await asyncio.wait_for(manager.delete("slow"), 3)
    manager._startup_limit.release()
    await asyncio.wait_for(manager.wait_for_agent_restore(), 3)
    assert manager._repos.agent_repo.get("slow") is None
    assert manager._octop_harness_or_none("slow") is None
    assert not workspace.exists()
    agent.aclose.assert_not_awaited()


@pytest.mark.parametrize("state", ["stopped", "failed"])
@pytest.mark.parametrize("command", ["/help", "/status"])
async def test_metadata_slash_commands_work_without_an_agent_runtime(
    manager: AgentManager, state: str, command: str
) -> None:
    from octop_gateway.models import ChannelSubject, InboundMessage, TextContent

    from octop.infra.gateway.process.processor import GlobalProcessor
    from octop.infra.gateway.slash.dispatcher import build_default_dispatcher
    from octop.infra.gateway.threads import ThreadRegistry

    repos = manager._repos
    user_id = repos.user_repo.create(username="user", password_hash="test", role="user")
    repos.agent_repo.create(agent_id="idle", user_id=user_id, name="Idle")
    repos.agent_repo.set_state("idle", state, error=None)
    await manager.boot(restore_agents=False)
    threads = ThreadRegistry(session_repo=repos.session_repo, thread_repo=repos.thread_repo)
    processor = GlobalProcessor(
        agent_manager=manager,
        thread_registry=threads,
        audit_repo=repos.audit_repo,
        agent_repo=repos.agent_repo,
        user_repo=repos.user_repo,
        connector_repo=repos.connector_repo,
        dispatcher=build_default_dispatcher(),
        usage_repo=repos.usage_repo,
        thread_message_repo=repos.thread_message_repo,
    )
    message = InboundMessage(
        channel_id="dashboard",
        channel_type="dashboard",
        tenant_id="idle",
        channel_subject=ChannelSubject(subject_id=str(user_id)),
        content=[TextContent(text=command)],
    )
    chunks = [chunk async for chunk in processor.iter_turn_chunks(message)]
    assert chunks[-1]["type"] == "done"
    assert any(chunk["type"] == "token" and chunk.get("content") for chunk in chunks)
    assert not any(chunk["type"] == "error" for chunk in chunks)
    assert repos.agent_repo.get("idle").last_state == state
    assert not manager._restore_scheduled
