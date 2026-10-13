"""Control-plane history handles, also usable before the engine is loaded."""

from __future__ import annotations

from typing import Any

from octop.infra.errors import ErrorCode, OctopError


def history_archive(server: Any) -> Any:
    if server.app_runtime is not None:
        return getattr(server.app_runtime, "history_archive", None)
    archive = getattr(server, "history_archive", None)
    return archive


def thread_registry(server: Any) -> Any:
    if server.app_runtime is not None:
        return server.app_runtime.gateway.thread_registry
    registry = getattr(server, "thread_registry", None)
    if registry is not None:
        return registry
    raise OctopError(ErrorCode.SERVICE_STARTING, "history storage is starting", status=503)


def agent_registry(server: Any) -> Any:
    return getattr(server.app_runtime, "agent_registry", None)
