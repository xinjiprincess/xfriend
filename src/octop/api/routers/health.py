"""Liveness probe (no auth)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from octop.api.deps import get_server, require_admin

router = APIRouter()


class HealthResponse(BaseModel):
    ok: bool
    application: str = "xfriend"
    protocol: int = 1
    ready: bool
    phase: str
    startup_id: str
    started_at: int | None
    db: bool
    users_loaded: int
    agents_running: int


class StartupResponse(BaseModel):
    startup_id: str
    phase: str
    ready: bool
    elapsed_ms: int
    checkpoints: dict[str, int]
    failure: str | None


@router.get(
    "/startup",
    summary="Administrator startup timings",
    response_model=StartupResponse,
    dependencies=[Depends(require_admin())],
)
async def startup(server: Any = Depends(get_server)) -> StartupResponse:
    """Credential-free monotonic startup checkpoints. Administrator access required."""
    return StartupResponse(**server.startup_progress.snapshot())


@router.get("", summary="Application identity and readiness", response_model=HealthResponse)
async def health(server: Any = Depends(get_server)) -> HealthResponse:
    """Readiness and bound control-plane state. No authentication required."""
    bound = server.database_bound
    users = server.user_manager.count() if server.user_manager is not None else 0
    agents = (
        sum(row.last_state == "running" for row in server.app_runtime.agent_registry.list_rows())
        if server.app_runtime is not None
        else 0
    )
    progress = server.startup_progress
    return HealthResponse(
        ok=True,
        ready=progress.ready,
        phase=progress.phase,
        startup_id=progress.startup_id,
        started_at=server._started_at,
        db=bound,
        users_loaded=users,
        agents_running=agents,
    )
