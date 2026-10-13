"""Keep partial startup distinct from first-run setup, login and activation."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.locale import resolve_request_locale

_CORE_GET = frozenset(
    {
        "/api/health",
        "/api/health/startup",
        "/api/docs",
        "/api/openapi.json",
        "/api/settings/timezone",
        "/api/settings/upload",
        "/api/settings/capabilities",
        "/api/settings/captcha",
        "/api/agents",
        "/api/experts",
        "/api/experts/published",
        "/api/providers",
        "/api/providers/presets",
        "/api/providers/resolved",
        "/api/providers/active-model",
        "/api/admin/providers",
        "/api/plugins",
        "/api/plugins/market",
        "/api/preferences",
        "/api/execution/experts",
        "/api/execution/templates",
    }
)
_CORE_HISTORY = re.compile(
    r"/api/agents/[^/]+/(?:threads|history-migration/status|"
    r"threads/[^/]+/(?:history(?:/export)?|document-preview|trajectory(?:/metrics|/export|/events/[^/]+)?))$"
)


def core_request_available(path: str, method: str) -> bool:
    """Control-plane routes whose handlers do not require execution SDK state."""
    if path.startswith(("/api/licensing/", "/api/auth/", "/api/i18n/")):
        return True
    if path.startswith("/api/setup/"):
        return path not in {"/api/setup/test-provider", "/api/setup/finish"}
    if method in {"GET", "HEAD"}:
        return (
            path in _CORE_GET
            or re.fullmatch(r"/api/agents/[^/]+", path) is not None
            or _CORE_HISTORY.fullmatch(path) is not None
        )
    return (
        method == "POST"
        and re.fullmatch(r"/api/agents/[^/]+/threads/[^/]+/(?:read|document)", path) is not None
    )


class StartupMiddleware:
    def __init__(
        self, app: ASGIApp, server: Any, optional_routes_pending: Callable[[], bool] | None = None
    ) -> None:
        self.app, self.server = app, server
        self.optional_routes_pending = optional_routes_pending

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        runtime_pending = (
            self.server._started
            and getattr(self.server, "runtime_ready", True) is False
            and not (
                scope["type"] == "http" and core_request_available(path, scope.get("method", "GET"))
            )
        )
        if (
            scope["type"] in {"http", "websocket"}
            and path.startswith("/api/")
            and path != "/api/health"
            and (not self.server._started or runtime_pending)
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                locale = resolve_request_locale(Request(scope))
                body = OctopError(ErrorCode.SERVICE_STARTING, "server is starting").to_envelope(
                    locale=locale
                )
                body["retry_after_ms"] = 500
                await JSONResponse(body, status_code=503, headers={"Retry-After": "1"})(
                    scope, receive, send
                )
            return
        if scope["type"] == "http" and path.startswith("/api/") and self.optional_routes_pending:
            replaced = False

            async def send_available(message: Message) -> None:
                nonlocal replaced
                if (
                    message["type"] == "http.response.start"
                    and message["status"] == 404
                    and self.optional_routes_pending is not None
                    and self.optional_routes_pending()
                ):
                    replaced = True
                    locale = resolve_request_locale(Request(scope))
                    body = OctopError(ErrorCode.SERVICE_STARTING, "routes are loading").to_envelope(
                        locale=locale
                    )
                    await JSONResponse(body, status_code=503, headers={"Retry-After": "1"})(
                        scope, receive, send
                    )
                elif not replaced:
                    await send(message)

            await self.app(scope, receive, send_available)
        else:
            await self.app(scope, receive, send)
