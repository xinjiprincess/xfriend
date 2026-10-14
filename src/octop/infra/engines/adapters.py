"""Thin adapters: native retains its gateway/history; DSH retains ACP checkpoints."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from octop_gateway.models import ChannelSubject, InboundMessage, TextContent

from octop.infra.engines.contracts import AskPermission, Emit, ExecutionContext
from octop.infra.engines.dsh import DSHService
from octop.infra.gateway.hitl.coordinator import HitlStreamContext
from octop.infra.gateway.hitl.format import is_ask_action_requests, normalize_hitl_request

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.gateway.process.processor import GlobalProcessor
    from octop.infra.gateway.threads import ThreadRegistry


class NativeEngine:
    id = "native"
    capabilities = frozenset(
        {
            "sessions",
            "resume",
            "cancel",
            "permission",
            "question",
            "history",
            "artifacts",
            "tools",
            "skills",
            "plugins",
            "connectors",
        }
    )

    def __init__(
        self, manager: AgentManager, processor: GlobalProcessor, threads: ThreadRegistry
    ) -> None:
        self.manager = manager
        self.processor = processor
        self.threads = threads
        self._threads: dict[tuple[int, str], str] = {}

    async def execute(
        self, context: ExecutionContext, text: str, emit: Emit, permission: AskPermission
    ) -> str:
        key = (context.user_id, context.session_id)
        session_key = f"execution:{context.user_id}:{context.session_id}"
        thread_id = await self.threads.get_or_create_by_key(
            session_key=session_key,
            agent_id=context.expert_id,
            user_id=context.user_id,
            channel_type="dashboard",
        )
        self._threads[key] = thread_id
        context = replace(context, session_id=thread_id)
        from octop import __version__

        await emit({"type": "session", "session_id": thread_id, "engine_version": __version__})
        message = InboundMessage(
            channel_id="xfriend-execution",
            channel_type="dashboard",
            tenant_id=context.expert_id,
            channel_subject=ChannelSubject(subject_id=str(context.user_id)),
            channel_session_id=session_key,
            content=[TextContent(text=text)],
            metadata={
                "user_id": context.user_id,
                "user_is_admin": context.is_admin,
                "thread_id": context.session_id,
                "session_key": session_key,
            },
        )
        stream = self.processor.iter_turn_chunks(message)
        output: list[str] = []
        while True:
            pending: dict[str, Any] | None = None
            async with aclosing(cast(AsyncGenerator[dict[str, Any], None], stream)):
                async for chunk in stream:
                    await emit(chunk)
                    if chunk.get("type") == "token":
                        output.append(str(chunk.get("content", "")))
                    elif chunk.get("type") == "error":
                        raise ValueError(str(chunk.get("error_code") or "ENGINE_EXECUTION_FAILED"))
                    elif chunk.get("type") == "hitl_required":
                        request = normalize_hitl_request(chunk.get("request"))
                        self.processor.hitl_coordinator.register_from_request(
                            request,
                            ctx=HitlStreamContext(
                                thread_id=thread_id,
                                agent_id=context.expert_id,
                                user_id=context.user_id,
                                session_key=session_key,
                                channel_type="dashboard",
                            ),
                        )
                        chunk["request"] = request
                        pending = chunk
            if pending is None:
                return "".join(output)
            answer = await permission(pending)
            if answer.get("decision") not in {"approve", "reject", "respond"}:
                raise ValueError("INVALID_PERMISSION_DECISION")
            request = pending.get("request") or {}
            actions = request.get("action_requests") or []
            if is_ask_action_requests(actions):
                if (
                    answer.get("decision") != "respond"
                    or not str(answer.get("message", "")).strip()
                ):
                    raise ValueError("QUESTION_ANSWER_REQUIRED")
                decisions = [{"type": "respond", "message": answer["message"]} for _ in actions]
            else:
                decisions = [{"type": answer["decision"]} for _ in actions]
            if not decisions:
                raise ValueError("INVALID_PERMISSION_REQUEST")
            coordinator = self.processor.hitl_coordinator
            record = coordinator.store.resolve_pending_for_thread(
                thread_id=context.session_id, agent_id=context.expert_id, user_id=context.user_id
            )
            if record is not None:
                coordinator.store.mark_resolved(
                    record.pending_id, "rejected" if answer["decision"] == "reject" else "approved"
                )
            stream = self.processor.iter_hitl_resume_chunks(
                agent_id=context.expert_id,
                thread_id=context.session_id,
                user_id=context.user_id,
                decisions=decisions,
            )

    async def cancel(self, context: ExecutionContext) -> None:
        thread_id = self._threads.get((context.user_id, context.session_id))
        if thread_id:
            self.manager.cancel_stream(context.expert_id, thread_id)

    async def close(self, context: ExecutionContext) -> None:
        # Native lifecycle belongs to AgentManager, not an individual task.
        self._threads.pop((context.user_id, context.session_id), None)


class DSHEngine:
    id = "dsh"
    capabilities = frozenset(
        {
            "sessions",
            "resume",
            "cancel",
            "permission",
            "tools",
            "mcp",
            "connectors",
            "versioned-runtime",
        }
    )

    def __init__(self, service: DSHService) -> None:
        self.service = service
        self._sessions: dict[tuple[int, str], str] = {}

    async def execute(
        self, context: ExecutionContext, text: str, emit: Emit, permission: AskPermission
    ) -> str:
        key = (context.user_id, context.session_id)
        session_id = context.engine_session_id or self._sessions.get(key)
        if session_id:
            session = await self.service.get(context.user_id, session_id)
        else:
            session = await self.service.create(context.user_id, context.workspace)
            self._sessions[key] = session.id
        self._sessions[key] = session.id
        await emit({"type": "session", "session_id": session.id})
        await self.service.prompt(session, text)
        assert session.task is not None
        observed = ""
        observed_tools = 0
        try:
            while not session.task.done():
                if session.messages and session.messages[-1]["role"] == "assistant":
                    tools = session.messages[-1].get("tools", [])
                    for event in tools[observed_tools:]:
                        await emit({"type": "tool", "detail": event})
                    observed_tools = len(tools)
                if session.messages and session.messages[-1]["role"] == "assistant":
                    current = str(session.messages[-1].get("text", ""))
                    if current != observed:
                        await emit({"type": "token", "content": current[len(observed) :]})
                        observed = current
                if session.state == "permission" and session.permission:
                    answer = await permission(session.permission)
                    await self.service.approve(session, str(answer.get("option_id", "")))
                else:
                    await asyncio.sleep(0.05)
            await session.task
            if session.state != "idle":
                raise ValueError("ENGINE_EXECUTION_FAILED")
            await emit(
                {
                    "type": "engine_version",
                    "engine_version": (session.engine_identity or {}).get("version", "unversioned"),
                }
            )
            result = str(session.messages[-1].get("text", ""))
            for event in session.messages[-1].get("tools", [])[observed_tools:]:
                await emit({"type": "tool", "detail": event})
            if result != observed:
                await emit({"type": "token", "content": result[len(observed) :]})
            return result
        except BaseException:
            await self.service.cancel(session)
            raise

    async def cancel(self, context: ExecutionContext) -> None:
        session_id = self._sessions.get((context.user_id, context.session_id))
        if session_id:
            await self.service.cancel(await self.service.get(context.user_id, session_id))

    async def close(self, context: ExecutionContext) -> None:
        session_id = self._sessions.pop((context.user_id, context.session_id), None)
        if session_id:
            await self.service.close(await self.service.get(context.user_id, session_id))
