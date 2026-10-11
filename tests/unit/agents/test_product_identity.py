from unittest.mock import AsyncMock, Mock

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from octop.infra.agents.middleware.product_identity import (
    PRODUCT_IDENTITY,
    ProductIdentityMiddleware,
)


@pytest.mark.parametrize(
    "system_message",
    [
        None,
        SystemMessage(content="You are a mathematics expert on Octop."),
        SystemMessage(
            content=[{"type": "text", "text": "Legacy Octop skill"}],
            additional_kwargs={"custom": "preserve"},
        ),
    ],
)
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_identity_reaches_model_without_rewriting_user_content(
    system_message: SystemMessage | None, asynchronous: bool
) -> None:
    messages = [
        HumanMessage(content="Explain `import octop`; quote Octop exactly."),
        AIMessage(content="I run on Octop."),
    ]
    request = ModelRequest(
        model=ChatOpenAI(model="test", api_key="test"),
        messages=messages,
        system_message=system_message,
    )
    before = system_message.model_copy(deep=True) if system_message else None
    middleware = ProductIdentityMiddleware()
    handler = AsyncMock() if asynchronous else Mock()
    if asynchronous:
        await middleware.awrap_model_call(request, handler)
    else:
        middleware.wrap_model_call(request, handler)
    forwarded = handler.call_args.args[0]
    content = forwarded.system_message.content
    assert PRODUCT_IDENTITY in forwarded.system_message.text
    assert "Singu Xfriend" in str(content)
    assert forwarded.messages == messages
    assert request.system_message == before
    if system_message is not None:
        assert forwarded.system_message.additional_kwargs == system_message.additional_kwargs
        if isinstance(system_message.content, list):
            assert content[:-1] == system_message.content
        else:
            assert content.startswith(system_message.content)
