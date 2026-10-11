"""Optional platform account and PCMATE device directory."""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from octop.api.deps import get_server, require_admin
from octop.infra.connectors.platform_collaboration import PlatformCollaboration
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/platform-collaboration")


class LoginBody(BaseModel):
    origin: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class TenantBody(BaseModel):
    tenant_id: int = Field(gt=0)


class PlatformView(BaseModel):
    origin: str
    logged_in: bool
    user: dict[str, Any] | None
    tenants: list[dict[str, Any]]


class DeviceView(BaseModel):
    id: int | str
    name: str | None = None
    description: str | None = None
    isActive: bool | int | None = None


def service(server: OctopServer = Depends(get_server)) -> PlatformCollaboration:
    return server.platform_collaboration


@router.get("", response_model=PlatformView, summary="Read platform connection status")
async def status(
    user: User = Depends(require_admin()), platform: PlatformCollaboration = Depends(service)
) -> Any:
    account = await platform.account(user.id)
    if account.token:
        try:
            await platform.devices(user.id)
        except (ValueError, httpx.HTTPError):
            account.user = None
    return account.view()


@router.post("/login", response_model=PlatformView, summary="Connect your platform account")
async def login(
    body: LoginBody,
    user: User = Depends(require_admin()),
    platform: PlatformCollaboration = Depends(service),
) -> Any:
    try:
        return await platform.login(user.id, body.origin, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail="PLATFORM_UNAVAILABLE") from exc


@router.post("/tenant", response_model=PlatformView, summary="Select a platform tenant")
async def tenant(
    body: TenantBody,
    user: User = Depends(require_admin()),
    platform: PlatformCollaboration = Depends(service),
) -> Any:
    try:
        return await platform.tenant(user.id, body.tenant_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail="PLATFORM_UNAVAILABLE") from exc


@router.get(
    "/devices", response_model=list[DeviceView], summary="List your PCMATE platform devices"
)
async def devices(
    user: User = Depends(require_admin()), platform: PlatformCollaboration = Depends(service)
) -> Any:
    try:
        return await platform.devices(user.id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail="PLATFORM_UNAVAILABLE") from exc


@router.post("/logout", response_model=PlatformView, summary="Disconnect the platform account")
async def logout(
    user: User = Depends(require_admin()), platform: PlatformCollaboration = Depends(service)
) -> Any:
    return await platform.logout(user.id)
