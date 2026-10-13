"""Slash command parsing and dispatch."""

from typing import TYPE_CHECKING, Any

from octop.infra.gateway.slash.ctx import SlashCtx, build_slash_ctx

if TYPE_CHECKING:
    from octop_harness.slash import BufferSink, SlashCommand, SlashSink

    from octop.infra.gateway.slash.dispatcher import SlashDispatcher, build_default_dispatcher
    from octop.infra.gateway.slash.parser import parse_slash


def __getattr__(name: str) -> Any:
    if name in {"BufferSink", "SlashCommand", "SlashSink"}:
        from octop_harness import slash

        return getattr(slash, name)
    if name in {"SlashDispatcher", "build_default_dispatcher"}:
        from octop.infra.gateway.slash import dispatcher

        return getattr(dispatcher, name)
    if name == "parse_slash":
        from octop.infra.gateway.slash.parser import parse_slash

        return parse_slash
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BufferSink",
    "SlashCommand",
    "SlashCtx",
    "SlashDispatcher",
    "SlashSink",
    "build_default_dispatcher",
    "build_slash_ctx",
    "parse_slash",
]
