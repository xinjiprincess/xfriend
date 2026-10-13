"""Authenticated portable configuration endpoints (no runtime boot during import)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.transfer.archive import MAX_ARCHIVE
from octop.infra.transfer.models import CommitRequest, ExportSelection, ImportPlan, Package
from octop.infra.transfer.service import PortablePackService
from octop.infra.users.identity import User

router = APIRouter(prefix="/transfer", tags=["settings"])


class PreviewFile(BaseModel):
    expert: str
    path: str
    size: int
    sample: str
    possible_secret: bool


class JobResponse(BaseModel):
    id: str
    state: str
    kind: str
    created_at: float
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None
    result: dict[str, Any] | None = None
    preview: list[PreviewFile] = Field(default_factory=list)


class InventoryResponse(BaseModel):
    agents: list[dict[str, Any]]
    providers: list[dict[str, Any]]
    voice_providers: list[dict[str, Any]]
    can_manage_models: bool


class InspectionResponse(BaseModel):
    id: str
    package: Package


class PlanResponse(BaseModel):
    id: str
    revision: str


def service(server: Any = Depends(get_server)) -> PortablePackService:
    if server.app_runtime is None:
        raise OctopError.localized(ErrorCode.SERVICE_STARTING)
    existing = getattr(server, "_portable_pack_service", None)
    if not isinstance(existing, PortablePackService):

        async def bind(user: User, agent_id: str, engine: str, workspace: str) -> Any:
            return await server.execution_service.bind(user, agent_id, engine, workspace)

        existing = PortablePackService(server.services, server.app_runtime.agent_registry, bind)
        server._portable_pack_service = existing
    return existing


@router.get(
    "/inventory",
    response_model=InventoryResponse,
    summary="List portable resources without credentials",
)
async def inventory(
    user: User = Depends(current_user), packs: PortablePackService = Depends(service)
) -> dict[str, Any]:
    return await packs.inventory(user)


@router.post(
    "/exports",
    response_model=JobResponse,
    summary="Export selected configurations in a background job",
    description="Teams include their owned members. Including API keys requires providers permission and a migration passphrase. Login tokens and licenses are never exported.",
)
async def export(
    body: ExportSelection,
    user: User = Depends(current_user),
    packs: PortablePackService = Depends(service),
) -> dict[str, Any]:
    return await packs.start_export(user, body)


@router.get("/jobs/{identity}", response_model=JobResponse, summary="Read your transfer job status")
async def job(
    identity: str, user: User = Depends(current_user), packs: PortablePackService = Depends(service)
) -> dict[str, Any]:
    return await packs.job(user, identity)


@router.get(
    "/exports/{identity}/file",
    summary="Download your completed portable package",
    response_class=FileResponse,
)
async def download(
    identity: str, user: User = Depends(current_user), packs: PortablePackService = Depends(service)
) -> FileResponse:
    return FileResponse(
        await packs.download(user, identity),
        media_type="application/octet-stream",
        filename="Xfriend-config.xfpack",
    )


@router.post(
    "/imports/inspect",
    response_model=InspectionResponse,
    summary="Validate and preview a package without creating resources",
)
async def inspect(
    file: UploadFile = File(...),
    password: str = Form(default="", max_length=1024),
    user: User = Depends(current_user),
    packs: PortablePackService = Depends(service),
) -> dict[str, Any]:
    data = await file.read(MAX_ARCHIVE + 1)
    await file.close()
    if len(data) > MAX_ARCHIVE:
        raise packs.error(user)
    return await packs.inspect(user, data, password)


@router.post(
    "/imports/{identity}/plan",
    response_model=PlanResponse,
    summary="Save mapping choices and issue a plan revision",
)
async def plan(
    identity: str,
    body: ImportPlan,
    user: User = Depends(current_user),
    packs: PortablePackService = Depends(service),
) -> dict[str, Any]:
    return await packs.plan(user, identity, body)


@router.post(
    "/imports/{identity}/commit",
    summary="Import a validated plan; identical retries return the same result",
)
async def commit(
    identity: str,
    body: CommitRequest,
    user: User = Depends(current_user),
    packs: PortablePackService = Depends(service),
) -> dict[str, Any]:
    return await packs.commit(user, identity, body.revision, body.password)


@router.delete(
    "/imports/{identity}",
    status_code=204,
    summary="Cancel your uncommitted import and remove its staged file",
)
async def cancel(
    identity: str, user: User = Depends(current_user), packs: PortablePackService = Depends(service)
) -> None:
    await packs.cancel(user, identity)
