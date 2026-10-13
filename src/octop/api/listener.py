"""Keep the Windows HTTP listener alive after an IOCP accept failure."""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from typing import Any

import uvicorn

logger = logging.getLogger(__name__)


class ResilientServer(uvicorn.Server):
    """Repair a lost listener without restarting agents or replaying requests.

    CPython issue #93821: Proactor closes the *listening* socket on WinError 64,
    while the event loop and uvicorn main loop continue running. Retain the
    original bind address so recovery also works with an automatically chosen port.
    """

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        self._listener_bindings: list[tuple[Any, str, int, int]] = []
        await super().startup(sockets=sockets)
        if sys.platform != "win32" or not self.started or sockets is not None:
            return
        if self.config.uds is not None or self.config.fd is not None:
            return
        for listener in self.servers:
            # A concrete Windows TCP bind owns one socket. Do not replace a
            # wildcard/multi-address listener that still has healthy sockets.
            if len(listener.sockets) == 1:
                sock = listener.sockets[0]
                host, port = sock.getsockname()[:2]
                self._listener_bindings.append((listener, host, port, sock.family))

    async def on_tick(self, counter: int) -> bool:
        exiting = await super().on_tick(counter)
        if exiting or self.should_exit or self.force_exit or counter % 10:
            return exiting
        for index, (listener, host, port, family) in enumerate(self._listener_bindings):
            # is_serving() stays true after the Proactor closes its socket.
            if listener.sockets and all(sock.fileno() >= 0 for sock in listener.sockets):
                continue

            def create_protocol() -> asyncio.Protocol:
                return self.config.http_protocol_class(  # type: ignore[call-arg]
                    config=self.config,
                    server_state=self.server_state,
                    app_state=self.lifespan.state,
                )

            try:
                replacement = await asyncio.get_running_loop().create_server(
                    create_protocol,
                    host=host,
                    port=port,
                    family=family,
                    ssl=self.config.ssl,
                    backlog=self.config.backlog,
                )
            except OSError:
                logger.warning("HTTP listener recovery failed for %s:%s", host, port, exc_info=True)
                continue
            listener.close()  # Existing connections continue on their own transports.
            # Keep the old server for uvicorn's graceful drain of active streams.
            self.servers.append(replacement)
            self._listener_bindings[index] = (replacement, host, port, family)
            logger.warning(
                "Recovered HTTP listener at %s:%s without restarting runtime", host, port
            )
        return exiting
