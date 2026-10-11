"""ACP client loaded only when a DSH process is requested."""

from __future__ import annotations

import asyncio
from typing import Any

from octop_harness.acp.client import ACPHostedClient


class StreamingClient(ACPHostedClient):
    """Keep the harness permission policy, and expose committed text updates."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pending_updates = 0
        self.drained = asyncio.Event()
        self.drained.set()

    def observe(self, event: Any) -> None:
        if event.direction == "incoming" and event.message.get("method") == "session/update":
            self.pending_updates += 1
            self.drained.clear()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        from acp.schema import AgentMessageChunk, TextContentBlock

        try:
            if isinstance(update, AgentMessageChunk) and isinstance(
                update.content, TextContentBlock
            ):
                await self._emit_message({"type": "text", "text": update.content.text}, False)
            else:
                await super().session_update(session_id, update, **kwargs)
        finally:
            self.pending_updates -= 1
            if self.pending_updates == 0:
                self.drained.set()
