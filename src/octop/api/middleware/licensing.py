"""Enforce the product entitlement for HTTP and WebSocket entry points."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from octop.infra.licensing.service import product_license

_OPEN = ("/api/licensing/", "/api/health/", "/api/setup/", "/api/auth/", "/api/i18n/")


class LicensingMiddleware:
    def __init__(self, app: ASGIApp, home: Path) -> None:
        self.app, self.home = app, home

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] in {"http", "websocket"}
            and path.startswith("/api/")
            and path not in {"/api/health", "/api/docs", "/api/openapi.json"}
            and not path.startswith(_OPEN)
        ):
            status = await product_license(self.home).status()
            if not status.valid:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4402})
                else:
                    await JSONResponse(
                        {"license_required": True, "state": status.state}, status_code=402
                    )(scope, receive, send)
                return
        if scope["type"] != "websocket" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        closed = False

        async def check_connection() -> bool:
            nonlocal closed
            if closed:
                return False
            if not (await product_license(self.home).status()).valid:
                closed = True
                await send({"type": "websocket.close", "code": 4402})
                return False
            return True

        async def licensed_receive() -> Message:
            while await check_connection():
                try:
                    message = await asyncio.wait_for(receive(), timeout=30)
                except TimeoutError:
                    continue
                if await check_connection():
                    return message
            return {"type": "websocket.disconnect", "code": 4402}

        async def licensed_send(message: Message) -> None:
            if message["type"] == "websocket.close":
                if not closed:
                    await send(message)
            elif await check_connection():
                await send(message)

        await self.app(scope, licensed_receive, licensed_send)
