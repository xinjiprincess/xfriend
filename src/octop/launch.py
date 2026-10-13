"""Composition root — wire OctopServer, FastAPI, and uvicorn for ``octop run``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)


def _ensure_linux_bubblewrap() -> None:
    """Best-effort startup provisioning; never fail the ``octop run`` process."""
    try:
        from octop.infra.utils.bwrap import ensure_bubblewrap

        result = ensure_bubblewrap()
    except Exception:
        logger.warning("background bubblewrap provisioning failed", exc_info=True)
        return
    status = result.get("status")
    detail = result.get("detail")
    if status == "degraded":
        logger.warning("background bubblewrap provisioning degraded: %s", detail or "unknown")
    else:
        logger.info("background bubblewrap provisioning status=%s", status)


def _schedule_linux_bubblewrap_ensure() -> asyncio.Task[None] | None:
    """Run bubblewrap provisioning once in the background on Linux."""
    if not sys.platform.startswith("linux"):
        return None
    return asyncio.create_task(
        asyncio.to_thread(_ensure_linux_bubblewrap),
        name="ensure-linux-bubblewrap",
    )


async def _cancel_background_task(task: asyncio.Task[None] | None) -> None:
    """Drop a startup background task on shutdown; never raise."""
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _serve(server: Any) -> None:
    await server.serve()


async def _warmup_after_listen(servers: list[Any], srv: Any, app: Any = None) -> None:
    """Do not let runtime restoration delay the first accepted HTTP request."""
    while not all(server.started for server in servers):
        await asyncio.sleep(0.025)
    srv.start_background_services()
    if app is not None:
        from octop.api.app import complete_deferred_routes

        # Expert restoration takes priority over optional page imports. First-run
        # setup has no runtime task yet and can complete its routes immediately.
        if app.state.pending_router_specs and srv._background_startup_task is not None:
            await asyncio.shield(srv._background_startup_task)
        await complete_deferred_routes(app)


async def run_foreground(
    *,
    host: str | None,
    port: int | None,
    reload: bool,
    workers: int,
    log_level: str | None,
    ssl_certfile: str | None,
    ssl_keyfile: str | None,
) -> None:
    """Boot the domain server, serve the HTTP API, then shut down cleanly."""
    import uvicorn

    from octop.api.app import build_app
    from octop.api.listener import ResilientServer
    from octop.infra.server import OctopServer
    from octop.infra.setup.tls.http_companion import build_http_companion_app
    from octop.infra.setup.tls.listeners import build_listen_plan
    from octop.infra.setup.tls.store import resolve_tls_paths

    srv = OctopServer()
    await srv.start(defer_agents=True)
    bwrap_task = _schedule_linux_bubblewrap_ensure()
    # Greenfield deferral leaves services unset until /setup/database binds.
    cfg = srv.services.config if srv.services is not None else srv.config
    assert cfg is not None
    bind_host = host or cfg.bind_host
    bind_port = port or cfg.port
    if ssl_certfile is None and ssl_keyfile is None:
        ssl_certfile, ssl_keyfile = resolve_tls_paths(srv.paths.root, cfg.tls)

    plan = build_listen_plan(
        cfg,
        bind_host=bind_host,
        bind_port=bind_port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
    level = (log_level or cfg.log_level).lower()
    worker_count = 1 if plan.dual_listeners or reload else workers
    if plan.dual_listeners and (reload or workers > 1):
        logger.warning(
            "TLS dual-port mode forces workers=1 and disables reload",
        )
        reload = False

    app = build_app(srv, defer_optional_routes=os.environ.get("XFRIEND_DESKTOP_RUNTIME") == "1")
    servers: list[uvicorn.Server] = []

    if plan.dual_listeners:
        assert plan.https_port is not None
        assert plan.http_port is not None
        https_config = uvicorn.Config(
            app,
            host=plan.bind_host,
            port=plan.https_port,
            log_level=level,
            workers=worker_count,
            reload=False,
            ssl_certfile=plan.ssl_certfile,
            ssl_keyfile=plan.ssl_keyfile,
        )
        companion = build_http_companion_app(https_port=plan.https_port)
        http_config = uvicorn.Config(
            companion,
            host=plan.bind_host,
            port=plan.http_port,
            log_level=level,
            workers=1,
            reload=False,
        )
        servers.append(ResilientServer(https_config))
        servers.append(ResilientServer(http_config))
        logger.info(
            "Listening on https://%s:%s and http://%s:%s (ACME + redirect)",
            plan.bind_host,
            plan.https_port,
            plan.bind_host,
            plan.http_port,
        )
    else:
        single_config = uvicorn.Config(
            app,
            host=plan.bind_host,
            port=plan.http_port or bind_port,
            log_level=level,
            workers=worker_count,
            reload=reload,
            ssl_certfile=plan.ssl_certfile,
            ssl_keyfile=plan.ssl_keyfile,
        )
        servers.append(ResilientServer(single_config))

    from octop.infra.agents.memory.slim_control import MemorySlimControl

    memory_control = MemorySlimControl(srv)
    warmup_task = asyncio.create_task(_warmup_after_listen(servers, srv, app))
    try:
        await memory_control.start()
        await asyncio.gather(*(_serve(s) for s in servers))
    finally:
        await memory_control.close()
        await _cancel_background_task(warmup_task)
        await _cancel_background_task(bwrap_task)
        await srv.stop()


def run_foreground_blocking(**kwargs: Any) -> None:
    """Sync entry for Click commands and tests."""
    asyncio.run(run_foreground(**kwargs))
