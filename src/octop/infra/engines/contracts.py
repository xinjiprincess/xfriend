"""Transport-independent execution contract shared by native and ACP engines."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExecutionContext:
    user_id: int
    expert_id: str
    session_id: str
    workspace: str = ""
    engine_session_id: str | None = None
    is_admin: bool = False


Emit = Callable[[dict[str, Any]], Awaitable[None]]
AskPermission = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ExecutionEngine(Protocol):
    id: str
    capabilities: frozenset[str]

    async def execute(
        self, context: ExecutionContext, text: str, emit: Emit, permission: AskPermission
    ) -> str: ...

    async def cancel(self, context: ExecutionContext) -> None: ...

    async def close(self, context: ExecutionContext) -> None: ...
