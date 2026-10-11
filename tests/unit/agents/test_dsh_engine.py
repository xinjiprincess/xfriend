"""Exercise DSH's independent transport, persistence and permission lifecycle."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from octop.infra.engines.dsh import DSHService, Session


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
