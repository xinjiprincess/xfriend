"""Local customer journal. Cloud bearer tokens never enter Python or the renderer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from octop.api.deps import current_user, get_server
from octop.api.routers.transfer import service as pack_service
from octop.infra.customer_sync.config import configuration_package, stage_configuration
from octop.infra.customer_sync.models import Edit, Journal, PullResponse, PushResponse, Resolution
from octop.infra.customer_sync.service import CustomerSyncService
from octop.infra.transfer.models import ExportSelection
from octop.infra.transfer.service import PortablePackService
from octop.infra.users.identity import User

router = APIRouter(prefix="/customer-sync", tags=["settings"])
Space = Annotated[str, Query(min_length=1, max_length=64)]


def service(server: Any = Depends(get_server)) -> CustomerSyncService:
    existing = getattr(server, "_customer_sync_service", None)
    if not isinstance(existing, CustomerSyncService):
        existing = CustomerSyncService(server.services)
        server._customer_sync_service = existing
    return existing


@router.get("", response_model=Journal, summary="Read your customer sync journal")
async def snapshot(
    space_id: Space,
    user: User = Depends(current_user),
    sync: CustomerSyncService = Depends(service),
) -> Journal:
    return await sync.transact(user, space_id)


@router.post(
    "/entities",
    response_model=Journal,
    summary="Save a manual edit or explicitly confirmed AI proposal",
)
async def edit(
    body: Edit,
    space_id: Space,
    user: User = Depends(current_user),
    sync: CustomerSyncService = Depends(service),
) -> Journal:
    return await sync.edit(user, space_id, body)


@router.post(
    "/acknowledge",
    response_model=Journal,
    summary="Acknowledge cloud operations without advancing the pull cursor",
)
async def acknowledge(
    body: PushResponse,
    space_id: Space,
    user: User = Depends(current_user),
    sync: CustomerSyncService = Depends(service),
) -> Journal:
    return await sync.acknowledge(user, space_id, body)


@router.post(
    "/pull", response_model=Journal, summary="Atomically persist pulled entities and cursor"
)
async def ingest(
    body: PullResponse,
    space_id: Space,
    user: User = Depends(current_user),
    sync: CustomerSyncService = Depends(service),
) -> Journal:
    return await sync.ingest(user, space_id, body)


@router.post(
    "/resolve",
    response_model=Journal,
    summary="Confirm keeping the cloud version or rebasing a local edit",
)
async def resolve(
    body: Resolution,
    space_id: Space,
    user: User = Depends(current_user),
    sync: CustomerSyncService = Depends(service),
) -> Journal:
    return await sync.resolve(user, space_id, body)


@router.post(
    "/configuration",
    response_model=Journal,
    summary="Stage selected portable configurations for cloud sync",
    description="Uses existing ownership/provider checks. API keys require explicit include_secrets and providers permission. xfpack resource metadata and files survive the phone payload mapping.",
)
async def export_configuration(
    body: ExportSelection,
    space_id: Space,
    user: User = Depends(current_user),
    sync: CustomerSyncService = Depends(service),
    packs: PortablePackService = Depends(pack_service),
) -> Journal:
    return await stage_configuration(sync, packs, user, space_id, body)


@router.get(
    "/configuration/package",
    response_class=Response,
    summary="Download synced configurations as a key-free xfpack",
    description="Review and import through the existing transfer screen. Downloading does not install skills or change providers; unresolved team references must be synced first.",
)
async def download_configuration(
    space_id: Space,
    user: User = Depends(current_user),
    sync: CustomerSyncService = Depends(service),
) -> Response:
    return Response(
        await configuration_package(sync, user, space_id),
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="customer-sync.xfpack"'},
    )
