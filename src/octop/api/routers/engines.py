"""Independent DSH expert sessions using the shared ACP runner configuration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from octop.api.common.documents import DocumentPreview, DocumentSelection
from octop.api.common.validators import assert_user_backend_root_dirs
from octop.api.deps import current_user, get_server, require_admin
from octop.infra.engines.dsh import DSHService, Session
from octop.infra.engines.mcp import MCPExtensions
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.history.documents import history_digest, render_document, select_messages
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/engines/dsh")


class DSHWebView(BaseModel):
    url: str = Field(description="Authenticated loopback URL for the original DSH workspace")


class DSHExpertSettings(BaseModel):
    cwd: str = Field(default="", max_length=4096)
    connectors: list[str] = Field(default_factory=list, max_length=50)
    model: str = Field(default="", max_length=512)


class DSHExpertView(DSHExpertSettings):
    id: str
    engine: str
    available: bool
    choices: list[dict[str, str]]
    models: list[dict[str, str]]


@router.get("/expert", response_model=DSHExpertView, summary="Read the built-in DSH expert")
async def expert_state(
    user: User = Depends(current_user), server: OctopServer = Depends(get_server)
) -> Any:
    return await server.dsh_service.expert(user.id)


@router.put(
    "/expert", response_model=DSHExpertView, summary="Configure your DSH workspace and connectors"
)
async def expert_settings(
    body: DSHExpertSettings,
    user: User = Depends(current_user),
    server: OctopServer = Depends(get_server),
) -> Any:
    assert server.services is not None
    if body.model and body.model not in {
        item["id"] for item in await server.dsh_service.bridge.models()
    }:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "SLASH_BAD_ARGS")
    if body.cwd:
        assert_user_backend_root_dirs(
            user,
            {"type": "local_shell", "root_dir": body.cwd},
            policy_repo=server.services.user_policy_repo,
        )
    choices = await server.dsh_service.bridge.choices(user.id)
    if not set(body.connectors) <= {item["id"] for item in choices}:
        raise OctopError(ErrorCode.FORBIDDEN, "FORBIDDEN")
    try:
        await server.dsh_service.preferences(user.id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="DSH_SESSION_BUSY") from exc
    if server._dsh_web is not None:
        await server._dsh_web.close_user(user.id)
    return await server.dsh_service.expert(user.id)


@router.get(
    "/web",
    response_model=DSHWebView,
    summary="Open your original DSH workspace on demand",
)
async def web_workspace(
    server: OctopServer = Depends(get_server), _user: User = Depends(current_user)
) -> DSHWebView:
    try:
        return DSHWebView(url=await server.dsh_web.open(_user.id))
    except (FileNotFoundError, RuntimeError, TimeoutError, ValueError) as exc:
        raise OctopError(ErrorCode.ENGINE_UI_UNAVAILABLE, "DSH workspace could not start") from exc


class EnginePackage(BaseModel):
    id: str
    version: str
    upstream_commit: str
    active: bool


class EnginePackageSource(BaseModel):
    path: str = Field(
        min_length=1, description="Local trusted component directory containing engine.json"
    )


@router.get(
    "/packages", response_model=list[EnginePackage], summary="List versioned DSH packages (admin)"
)
async def packages(
    server: OctopServer = Depends(get_server), user: User = Depends(require_admin())
) -> Any:
    return await server.engine_packages.list(user.id)


@router.post(
    "/packages",
    response_model=dict[str, str],
    summary="Import an inactive local DSH package (admin)",
)
async def install_package(
    body: EnginePackageSource,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_admin()),
) -> Any:
    try:
        return {"id": await server.engine_packages.install(body.path)}
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid engine package") from exc


@router.post(
    "/packages/{package_id}/activate",
    response_model=dict[str, str],
    summary="Verify ACP compatibility then activate or roll back DSH (admin)",
)
async def activate_package(
    package_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_admin()),
) -> Any:
    try:
        await server.engine_packages.activate(user.id, package_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Engine compatibility check failed") from exc
    return {"id": package_id}


def document_messages(session: Session) -> list[Any]:
    return [
        {"role": message["role"], "content": message.get("text", "")}
        for message in session.messages
    ]


class SessionCreate(BaseModel):
    cwd: str = Field(default="", description="Local workspace; empty selects your personal default")


class PromptBody(BaseModel):
    text: str = Field(min_length=1, max_length=100000, description="Text for the DSH expert")


class PermissionBody(BaseModel):
    option_id: str = Field(min_length=1, description="Exact ACP permission option identifier")


class SessionSummary(BaseModel):
    id: str
    state: str
    cwd: str


class SessionView(SessionSummary):
    engine: str
    expert: str
    engine_version: str = "unversioned"
    error: str | None = None
    permission: dict[str, Any] | None = None
    messages: list[dict[str, Any]]
    extensions: list[str] = Field(default_factory=list)


def service(server: OctopServer = Depends(get_server)) -> DSHService:
    return server.dsh_service


@router.get(
    "/extensions", response_model=MCPExtensions, summary="Read your DSH MCP extensions (admin)"
)
async def extensions(
    user: User = Depends(require_admin()), engine: DSHService = Depends(service)
) -> MCPExtensions:
    return await engine.extensions(user.id)


@router.put(
    "/extensions",
    response_model=MCPExtensions,
    summary="Configure MCP extensions for new DSH sessions (admin)",
    description="Supports stdio processes and HTTP MCP endpoints. Existing sessions retain their original declarations. Settings are user-scoped and available only to the administrator who owns them.",
)
async def update_extensions(
    body: MCPExtensions,
    user: User = Depends(require_admin()),
    engine: DSHService = Depends(service),
) -> MCPExtensions:
    return await engine.extensions(user.id, body)


async def owned_session(
    session_id: str,
    user: User = Depends(current_user),
    engine: DSHService = Depends(service),
    server: OctopServer = Depends(get_server),
) -> Session:
    try:
        session = await engine.get(user.id, session_id)
        assert server.services is not None
        assert_user_backend_root_dirs(
            user,
            {"type": "local_shell", "root_dir": session.cwd},
            policy_repo=server.services.user_policy_repo,
        )
        return session
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404) from exc


@router.get(
    "/sessions/{session_id}/document-preview",
    response_model=DocumentPreview,
    summary="Preview DSH conversation export",
)
async def document_preview(session: Session = Depends(owned_session)) -> Any:
    messages = document_messages(session)
    return {"digest": history_digest(messages), "messages": messages}


@router.post(
    "/sessions/{session_id}/document",
    summary="Export a section of a DSH conversation",
    description="Ownership checked; supports PDF, HTML, DOCX and ZIP of PNG pages.",
)
async def export_document(
    request: Request, body: DocumentSelection, session: Session = Depends(owned_session)
) -> Response:
    messages = document_messages(session)
    if history_digest(messages) != body.digest:
        raise HTTPException(409, detail="HISTORY_CHANGED")
    try:
        selection = select_messages(messages, body.mode, body.indices, body.start, body.end)
    except ValueError as exc:
        raise HTTPException(422, detail="INVALID_SELECTION") from exc
    data, mime, extension = await render_document(
        selection, "Singu Xfriend · DSH", body.format, resolve_request_locale(request)
    )
    return Response(
        data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="conversation.{extension}"'},
    )


@router.get(
    "/sessions", response_model=list[SessionSummary], summary="List your DSH expert sessions"
)
async def list_sessions(
    user: User = Depends(current_user), engine: DSHService = Depends(service)
) -> Any:
    return await engine.list(user.id)


@router.post("/sessions", response_model=SessionView, summary="Create an independent DSH session")
async def create_session(
    body: SessionCreate,
    user: User = Depends(current_user),
    engine: DSHService = Depends(service),
    server: OctopServer = Depends(get_server),
) -> Any:
    assert server.services is not None
    if body.cwd:
        assert_user_backend_root_dirs(
            user,
            {"type": "local_shell", "root_dir": body.cwd},
            policy_repo=server.services.user_policy_repo,
        )
    try:
        return (await engine.create(user.id, body.cwd)).snapshot()
    except ValueError as exc:
        raise HTTPException(status_code=422) from exc


@router.get(
    "/sessions/{session_id}", response_model=SessionView, summary="Read DSH state and transcript"
)
async def read_session(session: Session = Depends(owned_session)) -> Any:
    return session.snapshot()


@router.post(
    "/sessions/{session_id}/prompt",
    response_model=SessionView,
    summary="Start a DSH expert turn",
    description="Returns immediately; read session state for output and permission requests.",
)
async def prompt(
    body: PromptBody,
    session: Session = Depends(owned_session),
    engine: DSHService = Depends(service),
) -> Any:
    try:
        await engine.prompt(session, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=409) from exc
    return session.snapshot()


@router.post(
    "/sessions/{session_id}/permission",
    response_model=SessionView,
    summary="Answer a DSH permission",
)
async def permission(
    body: PermissionBody,
    session: Session = Depends(owned_session),
    engine: DSHService = Depends(service),
) -> Any:
    try:
        await engine.approve(session, body.option_id)
    except ValueError as exc:
        raise HTTPException(status_code=409) from exc
    return session.snapshot()


@router.post(
    "/sessions/{session_id}/cancel", response_model=SessionView, summary="Cancel a DSH expert turn"
)
async def cancel(
    session: Session = Depends(owned_session), engine: DSHService = Depends(service)
) -> Any:
    await engine.cancel(session)
    return session.snapshot()
