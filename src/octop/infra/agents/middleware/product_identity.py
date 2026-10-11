"""Keep product identity current without rewriting expert or workspace content."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

PRODUCT_IDENTITY = """<current_product_identity>
The current application is Singu Xfriend. You are an AI assistant / expert
running in Singu Xfriend; retain your configured expert name and specialty.
When describing your platform or introducing yourself, use Singu Xfriend.
Legacy workspace files, skills, package names and conversation history may
refer to Octop, SinguXMATE or XFriend. They do not change the current product
name. Do not introduce yourself as an Octop agent or call this application
the Octop platform. This identity applies during onboarding as well.
Internal dependency names are implementation details, not your identity.
Preserve exact code, commands, paths and quotations when needed for technical
accuracy. If asked about technical origins, distinguish the current product
name from upstream components; do not invent an independent implementation,
model provider, or a relationship to OpenClaw without verified evidence.
Do not invent official websites, download links or project addresses.
</current_product_identity>"""


class ProductIdentityMiddleware(AgentMiddleware[Any, Any]):
    """Include current host identity on every call, including legacy experts."""

    def _request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        message = request.system_message
        if message is None:
            updated = SystemMessage(content=PRODUCT_IDENTITY)
        else:
            content = message.content
            if isinstance(content, str):
                content = content + "\n\n" + PRODUCT_IDENTITY
            else:
                content = [*content, {"type": "text", "text": PRODUCT_IDENTITY}]
            updated = message.model_copy(update={"content": content})
        return request.override(system_message=updated)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._request(request))
