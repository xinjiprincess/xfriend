"""Versioned local DSH packages; activation follows a keyless ACP compatibility gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from octop.infra.engines.dsh import DSHService
from octop.infra.errors import ErrorCode, OctopError


class EnginePackages:
    def __init__(
        self, home: Path, service: DSHService, assert_idle: Callable[[], None] | None = None
    ) -> None:
        self.root = home / "engine-packages" / "dsh"
        self.service = service
        self.lock = asyncio.Lock()
        self.assert_idle = assert_idle or (lambda: None)

    def _directory(self, identity: str) -> Path:
        if str(uuid.UUID(identity)) != identity:
            raise ValueError("Invalid engine package identifier")
        return self.root / identity

    def _runner(self, directory: Path) -> dict[str, Any]:
        metadata = json.loads((directory / "engine.json").read_text("utf-8"))
        if metadata.get("id") != "dsh" or metadata.get("transport") != "acp":
            raise ValueError("Unsupported engine transport")
        if not metadata.get("version") or not metadata.get("upstream_commit"):
            raise ValueError("Version and upstream commit required")

        def member(name: str) -> Path:
            path = (directory / name).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                raise ValueError("Invalid engine package member")
            return path

        checksums = metadata.get("sha256")
        if not isinstance(checksums, dict) or not checksums:
            raise ValueError("Engine checksums required")
        for name, expected in checksums.items():
            with member(name).open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("Engine checksum mismatch")
        runtime, entry = str(metadata["runtime"]), str(metadata["entry"])
        if runtime not in checksums or entry not in checksums:
            raise ValueError("Runtime and entrypoint must have checksums")
        return {
            "command": str(member(runtime)),
            "args": [str(member(entry)), "--profile", "acp"],
            "enabled": True,
            "trusted": False,
        }

    async def list(self, user_id: int) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            current = self.service.settings.load_runners(user_id)["dsh"]["command"]
            result = []
            for manifest in self.root.glob("*/engine.json"):
                metadata = json.loads(manifest.read_text("utf-8"))
                result.append(
                    {
                        "id": manifest.parent.name,
                        "version": str(metadata["version"]),
                        "upstream_commit": str(metadata["upstream_commit"]),
                        "active": Path(current).parent == manifest.parent,
                    }
                )
            return result

        return await asyncio.get_running_loop().run_in_executor(None, read)

    async def install(self, source: str) -> str:
        async with self.lock:
            identity = str(uuid.uuid4())
            destination = self._directory(identity)

            def copy() -> None:
                directory = Path(source).resolve()
                self._runner(directory)
                destination.parent.mkdir(parents=True, exist_ok=True)
                # A candidate remains inactive until the explicit activation gate succeeds.
                try:
                    shutil.copytree(directory, destination)
                    self._runner(destination)
                except BaseException:
                    if destination.exists():
                        shutil.rmtree(destination)
                    raise

            await asyncio.get_running_loop().run_in_executor(None, copy)
            return identity

    async def activate(self, user_id: int, identity: str) -> None:
        async with self.lock:
            self.assert_idle()
            directory = self._directory(identity)
            runner = await asyncio.get_running_loop().run_in_executor(None, self._runner, directory)
            if any(
                session.state in {"running", "permission"}
                for session in self.service.sessions.values()
            ):
                raise OctopError(
                    ErrorCode.AGENT_BUSY, "Wait for DSH tasks before switching engines"
                )

            class Settings:
                def load_runners(self, _user_id: int) -> dict[str, Any]:
                    return {"dsh": runner}

            # Never pass user credentials to a compatibility probe.
            with tempfile.TemporaryDirectory(prefix="xfriend-engine-check-") as temporary:
                candidate = DSHService(Path(temporary), Settings())  # type: ignore[arg-type]
                session = await candidate.create(user_id, temporary)
                try:
                    await candidate._open(session)
                    acp_id = session.acp_id
                    await candidate.close(session)
                    await candidate._open(session)
                    if session.acp_id != acp_id:
                        raise ValueError("Engine resume compatibility failed")
                finally:
                    await candidate.shutdown()

            self.assert_idle()

            def save() -> None:
                runners = self.service.settings.load_runners(user_id)
                runners["dsh"] = {**runners["dsh"], **runner}
                self.service.settings.save_runners(user_id, runners)

            await asyncio.get_running_loop().run_in_executor(None, save)
