"""Native adapter forwards identity and resumes questions through the gateway."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from octop.infra.engines.adapters import NativeEngine
from octop.infra.engines.contracts import ExecutionContext


async def test_native_question_resume_and_identity():
    inbound = []
    decisions = []

    async def stream(message):
        inbound.append(message)
        yield {
            "type": "hitl_required",
            "request": {
                "action_requests": [
                    {
                        "name": "ask_user_question",
                        "args": {"questions": [{"question": "Which file?"}]},
                    }
                ]
            },
        }

    async def resume(**kwargs):
        decisions.extend(kwargs["decisions"])
        yield {"type": "token", "content": "done"}

    coordinator = MagicMock()
    coordinator.store.resolve_pending_for_thread.return_value = SimpleNamespace(
        pending_id="pending"
    )
    processor = SimpleNamespace(
        iter_turn_chunks=stream, iter_hitl_resume_chunks=resume, hitl_coordinator=coordinator
    )
    manager = MagicMock()
    adapter = NativeEngine(
        manager, processor, SimpleNamespace(get_or_create_by_key=AsyncMock(return_value="thread"))
    )
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def answer(request):
        return {"decision": "respond", "message": "readme.md"}

    context = ExecutionContext(7, "expert", "logical")
    assert await adapter.execute(context, "task", emit, answer) == "done"
    assert inbound[0].channel_subject.subject_id == "7"
    assert inbound[0].metadata["user_is_admin"] is False
    assert decisions == [{"type": "respond", "message": "readme.md"}]
    await adapter.cancel(context)
    manager.cancel_stream.assert_called_once_with("expert", "thread")
    await adapter.close(context)
