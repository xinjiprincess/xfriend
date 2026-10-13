"""Public desktop activation endpoints; account auth remains a separate concern."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from octop.api.deps import get_server
from octop.infra.licensing.service import LicenseStatus, product_license
from octop.infra.server import OctopServer

router = APIRouter(prefix="/licensing")


def localized_status(server: OctopServer, result: LicenseStatus) -> LicenseStatus:
    config = server.services.config if server.services else server.config
    if config is not None:
        result.timezone = config.default_timezone
    return result


class ActivationRequest(BaseModel):
    code: str | None = Field(default=None, min_length=8, max_length=128)


def local_activation_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "Cross-origin activation is forbidden")
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Activate from this computer")


@router.get("/status", response_model=LicenseStatus, summary="Read this computer's product license")
async def status(server: OctopServer = Depends(get_server)) -> LicenseStatus:
    return localized_status(server, await product_license(server.paths.root).status())


@router.post(
    "/sync",
    response_model=LicenseStatus,
    summary="Claim the machine's trial or synchronize an existing entitlement",
    dependencies=[Depends(local_activation_request)],
)
async def sync(server: OctopServer = Depends(get_server)) -> LicenseStatus:
    return localized_status(server, await product_license(server.paths.root).synchronize())


@router.post(
    "/activate",
    response_model=LicenseStatus,
    summary="Refresh an administrator grant or redeem an activation code",
    dependencies=[Depends(local_activation_request)],
)
async def activate(
    body: ActivationRequest, server: OctopServer = Depends(get_server)
) -> LicenseStatus:
    return localized_status(
        server, await product_license(server.paths.root).synchronize(body.code, force=True)
    )
