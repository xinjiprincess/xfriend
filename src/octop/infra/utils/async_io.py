"""Finish an executor write before propagating cancellation to its caller."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def finish_io[T](write: Callable[[], T]) -> T:
    operation = asyncio.get_running_loop().run_in_executor(None, write)
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        # Cancelling an await cannot stop its worker thread. Keep locks held until
        # that write settles, including when shutdown sends another cancellation.
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                continue
        operation.result()
        raise
