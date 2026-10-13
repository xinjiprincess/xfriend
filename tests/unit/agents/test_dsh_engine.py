"""Exercise DSH's independent transport, persistence and permission lifecycle."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from octop.infra.engines.dsh import DSHService, Session
from octop.infra.engines.mcp import MCPExtension, MCPExtensions


class Settings:
    def load_runners(self, user_id: int) -> dict[str, Any]:
        return {
            "dsh": {
                "enabled": True,
                "command": sys.executable,
                "args": [str(Path(__file__).parents[2] / "support" / "fake_dsh.py")],
                "trusted": False,
            }
        }


@pytest.fixture
def engine(tmp_path: Path) -> DSHService:
    return DSHService(tmp_path, Settings())  # type: ignore[arg-type]


async def settle(session: Session) -> None:
    assert session.task is not None
    await asyncio.wait_for(asyncio.shield(session.task), timeout=45)
    assert session.state != "failed", session.error


@pytest.mark.asyncio
async def test_runtime_change_does_not_resume_old_checkpoint(tmp_path: Path) -> None:
    initial = DSHService(tmp_path, Settings())  # type: ignore[arg-type]
    session = await initial.create(7, str(tmp_path))
    try:
        await initial.prompt(session, "hello")
        await settle(session)
        assert session.engine_identity is not None
    finally:
        await initial.shutdown()

    class UpdatedSettings(Settings):
        def load_runners(self, user_id: int) -> dict[str, Any]:
            runners = super().load_runners(user_id)
            runners["dsh"]["args"].append("--new-runtime")
            return runners

    updated = DSHService(tmp_path, UpdatedSettings())  # type: ignore[arg-type]
    restored = await updated.get(7, session.id)
    try:
        await updated.prompt(restored, "continue")
        assert restored.task is not None
        await restored.task
        assert restored.state == "failed"
        assert restored.error == "ENGINE_VERSION_CHANGED"
        assert restored.process is None
        assert restored.acp_id == session.acp_id
    finally:
        await updated.shutdown()


async def state(session: Session, expected: str) -> None:
    async with asyncio.timeout(45):
        while session.state != expected:
            assert session.state != "failed", session.error
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_independent_prompt_and_resume(engine: DSHService, tmp_path: Path) -> None:
    session = await engine.create(7, str(tmp_path))
    try:
        await engine.prompt(session, "hello")
        await settle(session)
        assert session.messages[-1]["text"] == "reply:hello"
        await engine.shutdown()
        restored_engine = DSHService(tmp_path, Settings())  # type: ignore[arg-type]
        restored = await restored_engine.get(7, session.id)
        assert restored.messages == session.messages
        try:
            await restored_engine.prompt(restored, "again")
            await settle(restored)
            assert restored.messages[-1]["text"] == "reply:again"
            assert session.engine_identity is not None
            calls = (
                engine.root / "7" / "runtime" / session.engine_identity["signature"] / "calls.log"
            ).read_text()
            assert calls.count("session/new") == 1
            assert "session/resume" in calls
        finally:
            await restored_engine.shutdown()
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_permission_requires_exact_option(engine: DSHService, tmp_path: Path) -> None:
    session = await engine.create(3, str(tmp_path))
    try:
        await engine.prompt(session, "permission")
        await state(session, "permission")
        with pytest.raises(ValueError):
            await engine.approve(session, "invented-option")
        assert session.state == "permission"
        await engine.approve(session, "reject")
        await settle(session)
        assert session.permission is None
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_cancel_and_busy(engine: DSHService, tmp_path: Path) -> None:
    session = await engine.create(3, str(tmp_path))
    try:
        await engine.prompt(session, "wait")
        with pytest.raises(ValueError, match="busy"):
            await engine.prompt(session, "other")
        async with asyncio.timeout(10):
            while session.conn is None:
                await asyncio.sleep(0.02)
        await engine.cancel(session)
        assert session.state == "cancelled"
        await engine.prompt(session, "after cancel")
        await settle(session)
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_user_isolation_and_paths(engine: DSHService, tmp_path: Path) -> None:
    session = await engine.create(1, str(tmp_path))
    with pytest.raises(FileNotFoundError):
        await engine.get(2, session.id)
    assert await engine.list(2) == []
    with pytest.raises(ValueError):
        await engine.get(1, "../../other")
    with pytest.raises(ValueError):
        await engine.create(1, "relative-path")


async def test_pinned_runner_survives_default_version_switch(tmp_path: Path) -> None:
    initial = DSHService(tmp_path, Settings())  # type: ignore[arg-type]
    session = await initial.create(7, str(tmp_path))
    runner = Settings().load_runners(7)["dsh"]
    session.pinned_runner = {key: runner[key] for key in ("command", "args")}
    try:
        await initial.prompt(session, "hello")
        await settle(session)
    finally:
        await initial.shutdown()

    class Changed(Settings):
        def load_runners(self, user_id: int) -> dict[str, Any]:
            runners = super().load_runners(user_id)
            runners["dsh"]["command"] = str(tmp_path / "different-version")
            return runners

    restored = DSHService(tmp_path, Changed())  # type: ignore[arg-type]
    try:
        loaded = await restored.get(7, session.id)
        await restored.prompt(loaded, "continued")
        await settle(loaded)
        assert loaded.messages[-1]["text"] == "reply:continued"
        assert loaded.engine_identity == session.engine_identity
    finally:
        await restored.shutdown()


async def test_mcp_extensions_reach_acp_and_remain_pinned_on_resume(engine, tmp_path):
    original = MCPExtensions(
        extensions=[
            MCPExtension(
                name="worker",
                command=sys.executable,
                args=["tool.py"],
                env={"TOOL_KEY": "test-value"},
            ),
            MCPExtension(
                name="api",
                transport="http",
                url="https://mcp.example.test/api",
                headers={"Authorization": "Bearer test-value"},
            ),
        ]
    )
    await engine.extensions(7, original)
    assert (await engine.extensions(8)).extensions == []
    session = await engine.create(7, str(tmp_path))
    assert session.snapshot()["extensions"] == ["worker", "api"]
    assert "test-value" not in json.dumps(session.snapshot())
    try:
        await engine.prompt(session, "first")
        await settle(session)
    finally:
        await engine.shutdown()
    await engine.extensions(7, MCPExtensions())
    restored = DSHService(tmp_path, Settings())
    try:
        loaded = await restored.get(7, session.id)
        await restored.prompt(loaded, "second")
        await settle(loaded)
        calls_path = (
            engine.root / "7" / "runtime" / session.engine_identity["signature"] / "mcp-calls.jsonl"
        )
        calls = [json.loads(line) for line in calls_path.read_text("utf-8").splitlines()]
        assert [call["method"] for call in calls] == ["session/new", "session/resume"]
        assert calls[0]["servers"] == calls[1]["servers"]
        assert calls[0]["servers"][0]["env"] == [{"name": "TOOL_KEY", "value": "test-value"}]
        assert calls[0]["servers"][1]["headers"] == [
            {"name": "Authorization", "value": "Bearer test-value"}
        ]
        assert (await restored.create(7, str(tmp_path))).mcp_extensions == []
    finally:
        await restored.shutdown()


@pytest.mark.parametrize(
    "extension",
    [
        {"name": "missing-command"},
        {"name": "bad-url", "transport": "http", "url": "file:///private"},
        {
            "name": "bad-credentials",
            "transport": "http",
            "url": "https://name:password@example.test/mcp",
        },
        {
            "name": "bad-headers",
            "transport": "http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "one", "authorization": "two"},
        },
        {"name": "bad-env", "command": "worker", "env": {"A=B": "value"}},
    ],
)
def test_mcp_rejects_invalid_declarations(extension):
    with pytest.raises(ValueError):
        MCPExtension.model_validate(extension)
