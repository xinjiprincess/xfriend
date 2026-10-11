from types import SimpleNamespace

import pytest

from octop.api.routers.health import health
from octop.infra.startup import StartupProgress


@pytest.mark.asyncio
async def test_readiness_identifies_app_without_claiming_database_bound() -> None:
    progress = StartupProgress()
    server = SimpleNamespace(
        startup_progress=progress,
        database_bound=False,
        user_manager=None,
        app_runtime=None,
        _started_at=None,
    )
    result = await health(server)
    assert result.application == "xfriend"
    assert result.protocol == 1
    assert not result.ready
    progress.mark("configuration")
    progress.ready = True
    progress.mark("ready")
    result = await health(server)
    assert result.ready
    assert not result.db  # The first-run wizard itself is a ready UI.
    assert result.phase == "ready"
    assert progress.snapshot()["checkpoints"]["ready"] >= 0
