"""Mixed-engine tasks preserve bindings, ownership, and permission boundaries."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.engines.execution import ExecutionService
from octop.infra.errors import OctopError
from octop.infra.users.identity import User


class Engine:
    def __init__(self, identity: str, *, permission: bool = False, fail: bool = False):
        self.id = identity
        self.permission = permission
        self.fail = fail
        self.calls: list[Any] = []
        self.closed = 0

    async def execute(self, context, text, emit, permission):
        self.calls.append((context, text))
        await emit({"type": "session", "session_id": context.session_id})
        if self.permission:
            await permission({"options": [{"option_id": "allow", "name": "Allow"}]})
        if self.fail:
            raise RuntimeError("engine failed")
        await emit({"type": "token", "content": self.id})
        return self.id

    async def cancel(self, context):
        pass

    async def close(self, context):
        self.closed += 1


@pytest.fixture
def setup(tmp_path):
    values = {}
    rows = {
        name: SimpleNamespace(
            agent_id=name,
            name=name,
            enabled=True,
            user_id=1,
            is_shared=False,
            kind="agent",
            config_json="{}",
            system_prompt="",
        )
        for name in ["a", "b"]
    }
    repos = SimpleNamespace(
        agent_repo=SimpleNamespace(get=rows.get, list_all=lambda **kwargs: list(rows.values())),
        settings_repo=SimpleNamespace(
            get=values.get, set=lambda key, value: values.__setitem__(key, value)
        ),
    )
    native, dsh = Engine("native"), Engine("dsh")
    service = ExecutionService(tmp_path, repos, {"native": native, "dsh": dsh})
    return service, native, dsh, User(1, "admin", "admin", None)


async def finish(service, task):
    await service._running[(1, task["id"])]
    return await service.get(1, task["id"])


async def test_runtime_model_events_are_serializable(setup):
    from pydantic import BaseModel

    class Usage(BaseModel):
        input_tokens: int

    service, native, dsh, user = setup
    task = await service.create(user, ["a"], "task")
    await finish(service, task)
    await service._event(task, {"type": "usage", "usage": Usage(input_tokens=7)})
    persisted = await service.get(user.id, task["id"])
    assert persisted["events"][-1]["usage"] == {"input_tokens": 7}


async def test_sequential_binding_snapshot_and_followup(setup, tmp_path):
    service, native, dsh, user = setup
    await service.bind(user, "b", "dsh", str(tmp_path))
    task = await service.create(user, ["a", "b"], "task")
    await finish(service, task)
    assert task["state"] == "succeeded"
    assert [message.get("engine") for message in task["messages"][1:]] == ["native", "dsh"]
    assert "native" in dsh.calls[0][1]
    assert len({event["id"] for event in task["events"]}) == len(task["events"])
    await service.bind(user, "b", "native", "")
    await service.followup(user, task["id"], "again")
    await finish(service, task)
    assert len(dsh.calls) == 2
    assert dsh.calls[0][0].session_id == dsh.calls[1][0].session_id
    with pytest.raises(OctopError):
        await service.get(2, task["id"])


async def test_failure_stops_remaining_steps(setup):
    service, native, dsh, user = setup
    native.fail = True
    task = await service.create(user, ["a", "b"], "task")
    await finish(service, task)
    assert task["state"] == "failed"
    assert len(native.calls) == 1
    assert not dsh.calls
    assert native.closed == 1


async def test_permission_nonce_and_cancel(setup, tmp_path):
    service, native, dsh, user = setup
    dsh.permission = True
    await service.bind(user, "a", "dsh", str(tmp_path))
    task = await service.create(user, ["a"], "task")
    for _ in range(500):
        if task["state"] == "permission":
            break
        await asyncio.sleep(0.01)
    assert task["state"] == "permission"
    with pytest.raises(OctopError):
        await service.answer(user.id, task["id"], "stale", {"option_id": "allow"})
    await service.cancel(user.id, task["id"])
    assert task["state"] == "cancelled"
    assert task["permission"] is None


async def test_cancel_waits_for_pending_disk_write(setup, tmp_path, monkeypatch):
    service, _, dsh, user = setup
    dsh.permission = True
    await service.bind(user, "a", "dsh", str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    original = Path.write_text

    def slow_write(path, text, *args, **kwargs):
        if path.suffix == ".tmp" and json.loads(text).get("state") == "permission":
            entered.set()
            assert release.wait(10)
        return original(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", slow_write)
    task = await service.create(user, ["a"], "task")
    cancel = None
    try:
        assert await asyncio.get_running_loop().run_in_executor(None, entered.wait, 5)
        cancel = asyncio.create_task(service.cancel(user.id, task["id"]))
        await asyncio.sleep(0.05)
        assert not cancel.done()
    finally:
        release.set()
        if cancel is not None:
            await cancel
    assert json.loads(service._path(user.id, task["id"]).read_text("utf-8"))["state"] == "cancelled"


async def test_assistant_preferences_atomic_validation(setup):
    service, _, _, user = setup
    await service.assistant_preferences(user, ["a"], "hello")
    with pytest.raises(OctopError):
        await service.assistant_preferences(user, ["b"], " ")
    assert await service.assistant_preferences(user) == {"expert_ids": ["a"], "wake_word": "hello"}


async def test_queued_cancel_and_restart_do_not_repeat_steps(setup):
    service, native, _, user = setup
    await service._run_lock.acquire()
    try:
        task = await service.create(user, ["a"], "queued")
        await service.cancel(user.id, task["id"])
        assert task["state"] == "cancelled"
        assert not native.calls
    finally:
        service._run_lock.release()
    task = await service.create(user, ["a"], "restart")
    await finish(service, task)
    task["state"] = "permission"
    task["permission"] = {"id": "old"}
    await service._save(task)
    restarted = ExecutionService(service.root.parent, service.repos, service.engines)
    loaded = await restarted.get(user.id, task["id"])
    assert loaded["state"] == "interrupted"
    assert loaded["permission"] is None
    assert len(native.calls) == 1


async def test_dual_groups_synthesize_every_output_without_changing_bindings(setup, tmp_path):
    service, native, dsh, user = setup
    await service.bind(user, "a", "dsh", str(tmp_path))
    before = service._bindings(user.id)
    task = await service.create_dual(user, ["a"], ["b"], str(tmp_path), "a", "native", "deliver")
    await finish(service, task)
    assert task["state"] == "succeeded"
    assert task["mode"] == "dual"
    assert [step["engine"] for step in task["steps"]] == ["native", "dsh", "native"]
    assert task["steps"][-1]["phase"] == "summary"
    assert "[a · native]" in native.calls[-1][1]
    assert "[b · dsh]" in native.calls[-1][1]
    assert "deliver" in native.calls[-1][1]
    assert service._bindings(user.id) == before
    assert task["messages"][-1]["phase"] == "summary"
    await service.followup(user, task["id"], "refine")
    await finish(service, task)
    assert len(native.calls) == 4 and len(dsh.calls) == 2
    assert task["state"] == "succeeded"


async def test_dual_plan_rejects_inaccessible_expert_and_invalid_groups(setup, tmp_path):
    service, _, _, user = setup
    with pytest.raises(OctopError):
        await service.create_dual(
            User(2, "guest", "user", None), ["a"], ["b"], str(tmp_path), "a", "native", "task"
        )
    with pytest.raises(OctopError):
        await service.create_dual(user, [], ["b"], str(tmp_path), "a", "native", "task")
    with pytest.raises(OctopError):
        await service.create_dual(user, ["a", "a"], ["b"], str(tmp_path), "a", "native", "task")
    with pytest.raises(OctopError):
        await service.create_dual(user, ["a"], ["missing"], str(tmp_path), "a", "native", "task")
    with pytest.raises(OctopError):
        await service.create_dual(user, ["a"], ["b"], "relative", "a", "native", "task")
    assert await service.list_tasks(user.id) == []
