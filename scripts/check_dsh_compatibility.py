"""Keyless ACP compatibility gate before replacing the external DSH runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from octop.infra.engines.dsh import DSHService


class Settings:
    def __init__(self, command: str, args: list[str]) -> None:
        self.command, self.args = command, args

    def load_runners(self, user_id: int) -> dict[str, Any]:
        return {
            "dsh": {"enabled": True, "command": self.command, "args": self.args, "trusted": False}
        }


async def check(home: Path, command: str, args: list[str]) -> None:
    engine = DSHService(home, Settings(command, args))  # type: ignore[arg-type]
    session = await engine.create(1, str(home))
    try:
        await engine._open(session)
        identifier, upstream = session.id, session.acp_id
    finally:
        await engine.shutdown()
    restored = DSHService(home, Settings(command, args))  # type: ignore[arg-type]
    try:
        session = await restored.get(1, identifier)
        await restored._open(session)
        assert session.acp_id == upstream
    finally:
        await restored.shutdown()
    print(
        json.dumps(
            {
                "initialize": "passed",
                "new": "passed",
                "resume": "passed",
                "close": "passed",
                "live_model_task": "not_tested",
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="xfriend-dsh-compat-") as temporary:
        asyncio.run(check(Path(temporary), parsed.command, parsed.args))
