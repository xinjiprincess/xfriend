"""Owned expert bindings and sequential mixed-engine execution tasks."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.documents import DocumentPreview, DocumentSelection
from octop.api.deps import current_user, get_server
from octop.infra.agents.plugins.contributions import instantiate_expert
from octop.infra.engines.execution import ExecutionService
from octop.infra.history.documents import history_digest, render_document, select_messages
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/execution", tags=["agents"])


class ExpertTemplate(BaseModel):
    id: str
    plugin_id: str
    plugin_version: str
    name: str
    description: str
    engine: str
    system_prompt: str
    permissions: list[str]
    dependencies: list[str]


class TemplateCreate(BaseModel):
    plugin_id: str
    template_id: str
    name: str = Field(min_length=1, max_length=100)
    workspace: str = Field(default="", max_length=4096)


@router.get(
    "/templates",
    response_model=list[ExpertTemplate],
    summary="Preview enabled expert plugin templates",
)
async def templates(
    server: OctopServer = Depends(get_server), _user: User = Depends(current_user)
) -> Any:
    if server.plugin_manager is None:
        return []
    items = await asyncio.get_running_loop().run_in_executor(
        None, server.plugin_manager.contributions
    )
    return [item for item in items if item["type"] == "expert"]


@router.post(
    "/templates",
    summary="Create a separate expert from an accepted plugin template",
    response_model=dict[str, str],
)
async def create_template(
    body: TemplateCreate,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> Any:
    assert server.plugin_manager is not None and server.app_runtime is not None
    return await instantiate_expert(
        server.plugin_manager,
        server.app_runtime.agent_registry,
        server.execution_service,
        user,
        body.plugin_id,
        body.template_id,
        body.name,
        body.workspace,
    )


class ExpertBinding(BaseModel):
    engine: Literal["native", "dsh"] = "native"
    workspace: str = Field(
        default="", max_length=4096, description="DSH requires an absolute local workspace"
    )


class ExecutionExpert(ExpertBinding):
    id: str
    name: str
    capabilities: list[str] = Field(default_factory=list)


class TaskCreate(BaseModel):
    expert_ids: list[str] = Field(
        min_length=1, max_length=4, description="Ordered expert steps, executed sequentially"
    )
    text: str = Field(min_length=1, max_length=16000)


class TaskStep(ExpertBinding):
    expert_id: str
    name: str
    session_id: str
    engine_session_id: str | None = None
    engine_version: str | None = None


class TaskSummary(BaseModel):
    id: str
    state: str
    created_at: int
    updated_at: int
    steps: list[TaskStep]
    step: int
    error: str | None = None


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    sequence: int
    task_id: str
    step: int
    type: str


class TaskView(TaskSummary):
    messages: list[dict[str, Any]]
    events: list[ExecutionEvent]
    permission: dict[str, Any] | None = None


class PermissionAnswer(BaseModel):
    permission_id: str
    decision: Literal["approve", "reject", "respond"] | None = None
    message: str | None = Field(default=None, max_length=16000)
    option_id: str | None = None


class FollowupBody(BaseModel):
    text: str = Field(min_length=1, max_length=16000)


class AssistantTarget(BaseModel):
    expert_ids: list[str] = Field(default_factory=list, max_length=4)
    wake_word: str | None = Field(default=None, min_length=2, max_length=20)


def service(server: OctopServer = Depends(get_server)) -> ExecutionService:
    return server.execution_service


@router.get(
    "/assistant",
    response_model=AssistantTarget,
    summary="Read your assistant expert or team target",
)
async def assistant(
    user: User = Depends(current_user), execution: ExecutionService = Depends(service)
) -> Any:
    return await execution.assistant_preferences(user)


@router.put(
    "/assistant",
    response_model=AssistantTarget,
    summary="Save your assistant expert or ordered team target",
)
async def set_assistant(
    body: AssistantTarget,
    user: User = Depends(current_user),
    execution: ExecutionService = Depends(service),
) -> Any:
    return await execution.assistant_preferences(user, body.expert_ids, body.wake_word)


@router.post(
    "/tasks/{task_id}/input",
    response_model=TaskView,
    summary="Continue a completed task using its original engine bindings",
)
async def followup(
    task_id: str,
    body: FollowupBody,
    user: User = Depends(current_user),
    execution: ExecutionService = Depends(service),
) -> Any:
    return await execution.followup(user, task_id, body.text)


@router.get(
    "/experts",
    response_model=list[ExecutionExpert],
    summary="List experts and your engine bindings",
)
async def experts(
    user: User = Depends(current_user), execution: ExecutionService = Depends(service)
) -> Any:
    return await execution.experts(user)


@router.put(
    "/experts/{expert_id}",
    response_model=ExecutionExpert,
    summary="Bind an expert to an execution engine",
    description="Affects new tasks only. DSH remains administrator-only and requires a local workspace.",
)
async def bind(
    expert_id: str,
    body: ExpertBinding,
    user: User = Depends(current_user),
    execution: ExecutionService = Depends(service),
) -> Any:
    return await execution.bind(user, expert_id, body.engine, body.workspace)


@router.post(
    "/tasks",
    response_model=TaskView,
    status_code=201,
    summary="Run a single expert or sequential mixed-engine team",
    description="Returns immediately. Maximum four steps and ten minutes once execution starts. No automatic retry of side effects.",
)
async def create(
    body: TaskCreate,
    user: User = Depends(current_user),
    execution: ExecutionService = Depends(service),
) -> Any:
    return await execution.create(user, body.expert_ids, body.text)


@router.get("/tasks", response_model=list[TaskSummary], summary="List your execution tasks")
async def tasks(
    user: User = Depends(current_user), execution: ExecutionService = Depends(service)
) -> Any:
    return await execution.list_tasks(user.id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskView,
    summary="Read your task, transcript and ordered events",
)
async def task(
    task_id: str, user: User = Depends(current_user), execution: ExecutionService = Depends(service)
) -> Any:
    return await execution.get(user.id, task_id)


@router.post(
    "/tasks/{task_id}/cancel", response_model=TaskView, summary="Cancel only this owned task"
)
async def cancel(
    task_id: str, user: User = Depends(current_user), execution: ExecutionService = Depends(service)
) -> Any:
    return await execution.cancel(user.id, task_id)


@router.post(
    "/tasks/{task_id}/permission",
    response_model=TaskView,
    summary="Answer the current permission request",
)
async def answer(
    task_id: str,
    body: PermissionAnswer,
    user: User = Depends(current_user),
    execution: ExecutionService = Depends(service),
) -> Any:
    return await execution.answer(
        user.id, task_id, body.permission_id, body.model_dump(exclude_none=True)
    )


@router.get(
    "/tasks/{task_id}/document-preview",
    response_model=DocumentPreview,
    summary="Preview a task transcript export",
)
async def preview(
    task_id: str, user: User = Depends(current_user), execution: ExecutionService = Depends(service)
) -> Any:
    messages = await execution.document_messages(user.id, task_id)
    return {"digest": history_digest(messages), "messages": messages}


@router.post(
    "/tasks/{task_id}/document", summary="Export selected task messages as PDF, HTML, DOCX or PNG"
)
async def document(
    task_id: str,
    body: DocumentSelection,
    request: Request,
    user: User = Depends(current_user),
    execution: ExecutionService = Depends(service),
) -> Response:
    messages = await execution.document_messages(user.id, task_id)
    if history_digest(messages) != body.digest:
        raise HTTPException(409, "HISTORY_CHANGED")
    try:
        selection = select_messages(messages, body.mode, body.indices, body.start, body.end)
    except ValueError as exc:
        raise HTTPException(422, "INVALID_SELECTION") from exc
    data, mime, extension = await render_document(
        selection, "Singu Xfriend", body.format, resolve_request_locale(request)
    )
    return Response(
        data,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="task.{extension}"'},
    )
