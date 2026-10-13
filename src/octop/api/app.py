"""FastAPI app factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from scalar_fastapi import get_scalar_api_reference

from octop.api.middleware.jwt_auth import install as install_jwt_auth
from octop.api.middleware.setup_lockdown import install as install_setup_lockdown
from octop.api.openapi_meta import API_DESCRIPTION, OPENAPI_TAGS, configure_openapi
from octop.api.router_catalog import RouterSpec
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.utils.locale import resolve_request_locale

logger = logging.getLogger(__name__)


_NO_CACHE_DASHBOARD_NAMES = frozenset({"sw.js", "manifest.json", "index.html"})


def dashboard_cache_control(full_path: str) -> str | None:
    """Cache-Control for a dashboard SPA path, or ``None`` to leave unset."""
    name = Path(full_path).name.lower() if full_path else "index.html"
    if not full_path or name in _NO_CACHE_DASHBOARD_NAMES:
        return "no-cache"
    # Vite emits content-hashed files under assets/ — safe to pin forever.
    if full_path.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return None


def _dashboard_response(path: Path, full_path: str) -> FileResponse:
    response = FileResponse(path)
    cache_control = dashboard_cache_control(full_path)
    if cache_control is not None:
        response.headers["Cache-Control"] = cache_control
    return response


def is_dashboard_asset_path(full_path: str) -> bool:
    """True for hashed build artifacts that must 404 instead of falling back."""
    return full_path.startswith("assets/")


def _dashboard_fallback(index_file: Path, full_path: str) -> FileResponse:
    """Serve the SPA shell for routes; 404 for a missing hashed asset.

    Answering ``/assets/index.<old-hash>.js`` with ``index.html`` makes the
    browser reject HTML as a module script ("not a valid JavaScript MIME
    type"), which hides the real cause — a stale shell after an upgrade.
    """
    if is_dashboard_asset_path(full_path):
        raise HTTPException(status_code=404, detail="Not Found")
    return _dashboard_response(index_file, "")


def _mount_routers(app: FastAPI, mounts: Sequence[RouterSpec]) -> None:
    for spec in mounts:
        router = getattr(import_module(spec.module), spec.attribute)
        app.include_router(router, prefix=spec.prefix, tags=list(spec.tags))


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(OctopError)
    async def _octop(request: Request, exc: OctopError) -> JSONResponse:
        # Client responses are locale-localized; log the original English message
        # so INTERNAL_ERROR (and other 5xx) remain diagnosable in ~/.xfriend/logs.
        if exc.status >= 500:
            logger.error(
                "OctopError %s in %s: %s",
                exc.code.value,
                request.url.path,
                exc.message,
                exc_info=exc,
            )
        locale = resolve_request_locale(request)
        return JSONResponse(status_code=exc.status, content=exc.to_envelope(locale=locale))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception in %s", request.url.path)
        locale = resolve_request_locale(request)
        err = OctopError.localized(ErrorCode.INTERNAL_ERROR, locale)
        return JSONResponse(status_code=err.status, content=err.to_envelope())


async def complete_deferred_routes(app: FastAPI) -> None:
    """Construct optional routes off-loop, then insert before the SPA fallback."""
    pending = app.state.pending_router_specs
    if not pending:
        return

    def prepare() -> APIRouter:
        router = APIRouter()
        for spec in pending:
            child = getattr(import_module(spec.module), spec.attribute)
            router.include_router(child, prefix=spec.prefix, tags=list(spec.tags))
        return router

    router = await asyncio.to_thread(prepare)
    position = app.state.deferred_route_position
    previous_count = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[previous_count:]
    del app.router.routes[previous_count:]
    app.router.routes[position:position] = added
    app.state.pending_router_specs = []
    app.openapi_schema = None


def build_app(server: OctopServer, *, defer_optional_routes: bool = False) -> FastAPI:
    cfg = server.services.config if server.services else getattr(server, "config", None)
    enable_dashboard = cfg.enable_dashboard if cfg else True
    enable_api_docs = cfg.enable_api_docs if cfg else False
    enable_mobile = (
        cfg.capabilities.mobile.enabled if cfg and cfg.capabilities.mobile.enabled else False
    )

    app = FastAPI(
        title="Singu Xfriend API",
        version="0.1.0",
        description=API_DESCRIPTION,
        openapi_url="/api/openapi.json" if enable_api_docs else None,
        openapi_tags=OPENAPI_TAGS,
    )
    configure_openapi(app)
    app.state.octop_server = server
    _install_exception_handlers(app)

    if cfg and cfg.cors_origins:
        from octop.api.deps import ACCESS_TOKEN_RESPONSE_HEADER

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[ACCESS_TOKEN_RESPONSE_HEADER],
        )

    from octop.api.middleware.bridge_proxy import install as install_bridge_proxy

    # Bridge proxy must sit inside JWT auth so ``request.state.xfriend_user`` is set
    # (Starlette runs the last-added middleware first).
    install_bridge_proxy(app, server)
    install_jwt_auth(app, server)
    install_setup_lockdown(app, server)

    from octop.api.middleware.licensing import LicensingMiddleware
    from octop.infra.licensing.service import load_profile

    if load_profile()[1]:
        app.add_middleware(LicensingMiddleware, home=server.paths.root)

    from octop.api.middleware.startup import StartupMiddleware

    app.add_middleware(
        StartupMiddleware,
        server=server,
        optional_routes_pending=lambda: bool(app.state.pending_router_specs),
    )

    def bind_runtime(runtime: Any) -> None:
        if runtime.bridge_manager is None:
            return
        from octop.api.bridge_peer import prepare_peer_dashboard_turn
        from octop.api.routers.browser.stream import run_browser_stream_session

        runtime.bridge_manager.bind_asgi_app(
            app,
            peer_turn_runner=prepare_peer_dashboard_turn,
            peer_browser_runner=run_browser_stream_session,
        )

    server.register_runtime_ready_callback(bind_runtime)

    from octop.infra.setup.tls.challenge import challenge_store

    @app.get("/.well-known/acme-challenge/{token}", include_in_schema=False)
    async def acme_http01_challenge(token: str) -> PlainTextResponse:
        body = challenge_store.get(token)
        if body is None:
            raise HTTPException(status_code=404, detail="challenge not found")
        return PlainTextResponse(body)

    from octop.api.router_catalog import CORE_MODULES, MOBILE_ROUTER, ROUTERS

    specs = (*ROUTERS, MOBILE_ROUTER) if enable_mobile else ROUTERS
    initial = [spec for spec in specs if not defer_optional_routes or spec.module in CORE_MODULES]
    pending = [spec for spec in specs if spec not in initial]
    _mount_routers(app, initial)
    app.state.pending_router_specs = pending
    app.state.deferred_route_position = len(app.router.routes)

    if enable_api_docs:

        @app.get("/api/docs", include_in_schema=False)
        async def api_docs() -> HTMLResponse:
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title="Singu Xfriend API",
            )

    if enable_dashboard:
        dashboard_dir = Path(__file__).parent.parent / "dashboard"
        index_file = dashboard_dir / "index.html"
        if index_file.exists():

            @app.get("/{full_path:path}", include_in_schema=False)
            async def spa_fallback(full_path: str) -> FileResponse:
                if full_path.startswith(("api/", "ws/")):
                    raise HTTPException(status_code=404, detail="Not Found")

                if full_path:
                    raw_path = Path(full_path)
                    # Reject absolute paths and parent-dir references before
                    # joining, so user input never drives a path expression.
                    if raw_path.is_absolute() or ".." in raw_path.parts:
                        return _dashboard_fallback(index_file, full_path)
                    candidate = (dashboard_dir / Path(*raw_path.parts)).resolve()
                    try:
                        candidate.relative_to(dashboard_dir.resolve())
                    except ValueError:
                        return _dashboard_fallback(index_file, full_path)
                    if candidate.is_file():
                        return _dashboard_response(candidate, full_path)
                return _dashboard_fallback(index_file, full_path)

    return app
