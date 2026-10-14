"""Persistent, user-isolated DSH sessions over the upstream ACP protocol."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop.infra.agents.settings.acp import ACPSettingsStore
from octop.infra.engines.mcp import MCPExtensions
from octop.infra.engines.runtime import runner_identity
from octop.infra.utils.async_io import finish_io

if TYPE_CHECKING:
    from octop.infra.engines.acp_client import StreamingClient


@dataclass
class Session:
    user_id: int
    id: str
    cwd: str
    acp_id: str | None = None
    engine_identity: dict[str, str] | None = None
    isolated_runtime: bool = True
    pinned_runner: dict[str, Any] | None = None
    mcp_extensions: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    state: str = "idle"
    error: str | None = None
    permission: dict[str, Any] | None = None
    client: StreamingClient | None = None
    conn: Any = None
    process: Any = None
    stack: AsyncExitStack | None = None
    task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    save_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "engine": "dsh",
            "expert": "dsh-expert",
            "engine_version": (self.engine_identity or {}).get("version", "unversioned"),
            "cwd": self.cwd,
            "state": self.state,
            "error": self.error,
            "permission": self.permission,
            "messages": self.messages,
            "extensions": [item["name"] for item in self.mcp_extensions],
        }


class DSHService:
    """One lazy process per session; transcripts survive process and app restarts."""

    def __init__(self, root: Path, settings: ACPSettingsStore, bridge: Any = None) -> None:
        self.home = root
        self.root = root / "engine-data" / "dsh"
        self.settings = settings
        self.bridge = bridge
        self.sessions: dict[tuple[int, str], Session] = {}
        self._extensions_lock = asyncio.Lock()

    async def preferences(
        self, user_id: int, update: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        key = f"dsh_expert:{user_id}"
        repo = self.settings._settings
        if update is not None:
            for session in list(self.sessions.values()):
                if session.user_id == user_id:
                    if session.task is not None and not session.task.done():
                        raise ValueError("DSH session is busy")
                    await self._close(session)
            await asyncio.to_thread(repo.set, key, json.dumps(update))
        raw = await asyncio.to_thread(repo.get, key)
        return {"cwd": "", "connectors": [], "model": "", **(dict(json.loads(raw)) if raw else {})}

    async def expert(self, user_id: int) -> dict[str, Any]:
        import shutil

        runners = await asyncio.to_thread(self.settings.load_runners, user_id)
        command = str(runners["dsh"].get("command", ""))
        available = bool(command) and (
            Path(command).is_file()
            if Path(command).is_absolute()
            else shutil.which(command) is not None
        )
        preferences = await self.preferences(user_id)
        return {
            "id": "dsh-expert",
            "engine": "dsh",
            "available": available,
            "cwd": preferences["cwd"],
            "connectors": preferences["connectors"],
            "model": preferences["model"],
            "models": await self.bridge.models() if self.bridge else [],
            "choices": await self.bridge.choices(user_id) if self.bridge else [],
        }

    async def extensions(self, user_id: int, update: MCPExtensions | None = None) -> MCPExtensions:
        path = self.root / str(user_id) / "extensions.json"
        async with self._extensions_lock:
            if update is None:

                def read() -> MCPExtensions:
                    if not path.exists():
                        return MCPExtensions()
                    return MCPExtensions.model_validate_json(path.read_text("utf-8"))

                return await asyncio.get_running_loop().run_in_executor(None, read)
            text = update.model_dump_json()

            def write() -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(text, "utf-8")
                temporary.replace(path)

            await finish_io(write)
            return update

    def _folder(self, user_id: int, session_id: str) -> Path:
        # Only canonical UUIDs are accepted, never user-supplied filesystem paths.
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError("Invalid session id")
        return self.root / str(user_id) / session_id

    async def _save(self, session: Session) -> None:
        async with session.save_lock:
            await self._write(session)

    async def _write(self, session: Session) -> None:
        folder = self._folder(session.user_id, session.id)
        payload = json.dumps(
            {
                **session.snapshot(),
                "acp_id": session.acp_id,
                "engine_identity": session.engine_identity,
                "isolated_runtime": session.isolated_runtime,
                "pinned_runner": session.pinned_runner,
                "mcp_extensions": session.mcp_extensions,
            },
            ensure_ascii=False,
        )

        def write() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            temporary = folder / "session.json.tmp"
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(folder / "session.json")

        await finish_io(write)

    async def get(self, user_id: int, session_id: str) -> Session:
        folder = self._folder(user_id, session_id)
        key = (user_id, session_id)
        if key not in self.sessions:
            raw = await asyncio.get_running_loop().run_in_executor(
                None, lambda: json.loads((folder / "session.json").read_text(encoding="utf-8"))
            )
            loaded = Session(
                user_id=user_id,
                id=session_id,
                cwd=raw["cwd"],
                acp_id=raw.get("acp_id"),
                engine_identity=raw.get("engine_identity"),
                isolated_runtime=raw.get("isolated_runtime", False),
                pinned_runner=raw.get("pinned_runner"),
                mcp_extensions=raw.get("mcp_extensions", []),
                messages=raw.get("messages", []),
                state="interrupted"
                if raw.get("state") in {"running", "permission"}
                else raw.get("state", "idle"),
                error=raw.get("error"),
            )
            self.sessions.setdefault(key, loaded)
        return self.sessions[key]

    async def list(self, user_id: int) -> list[dict[str, Any]]:
        def ids() -> list[str]:
            return [p.parent.name for p in (self.root / str(user_id)).glob("*/session.json")]

        result = []
        for session_id in await asyncio.get_running_loop().run_in_executor(None, ids):
            session = await self.get(user_id, session_id)
            result.append({"id": session.id, "state": session.state, "cwd": session.cwd})
        return result

    async def create(self, user_id: int, cwd: str) -> Session:
        if not cwd:
            preferences = await self.preferences(user_id)
            cwd = str(preferences.get("cwd") or self.root / str(user_id) / "workspace")
            await asyncio.to_thread(Path(cwd).mkdir, parents=True, exist_ok=True)
        directory = Path(cwd).expanduser()
        if not directory.is_absolute():
            raise ValueError("Workspace must be an absolute local directory")
        exists = await asyncio.get_running_loop().run_in_executor(None, directory.is_dir)
        if not exists:
            raise ValueError("Workspace does not exist")
        configured = await self.extensions(user_id)
        session = Session(
            user_id=user_id,
            id=str(uuid.uuid4()),
            cwd=str(directory),
            mcp_extensions=[item.acp() for item in configured.extensions],
        )
        self.sessions[(user_id, session.id)] = session
        await self._save(session)
        return session

    async def _open(self, session: Session) -> None:
        from acp import PROTOCOL_VERSION, spawn_agent_process
        from acp.schema import (
            AcpMcpServer,
            HttpMcpServer,
            Implementation,
            McpServerStdio,
            SseMcpServer,
        )

        from octop.infra.engines.acp_client import StreamingClient

        if session.process is not None and session.process.returncode is None:
            return
        if session.stack is not None:
            await session.stack.aclose()
            session.stack = None
        runners = await asyncio.get_running_loop().run_in_executor(
            None, self.settings.load_runners, session.user_id
        )
        runner = dict(runners["dsh"])
        if session.pinned_runner is not None:
            runner.update(session.pinned_runner)
        else:
            packages = self.root.parents[1] / "engine-packages" / "dsh"
            if Path(str(runner.get("command", ""))).is_relative_to(packages):
                session.pinned_runner = {key: runner[key] for key in ("command", "args")}
        from octop_harness.acp.models import ACPRunnerConfig

        config = ACPRunnerConfig.from_dict(runner)
        if not config.enabled or not config.command.strip():
            raise ValueError("DSH runner is disabled or has no command")
        identity = await asyncio.get_running_loop().run_in_executor(None, runner_identity, runner)
        if session.engine_identity is not None and session.engine_identity != identity:
            raise ValueError("ENGINE_VERSION_CHANGED")
        session.engine_identity = identity
        await self._save(session)
        data_dir = self.root / str(session.user_id) / "runtime"
        if session.isolated_runtime:
            data_dir = data_dir / identity["signature"]
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: data_dir.mkdir(parents=True, exist_ok=True)
        )
        # DSH mutable state is outside the installation and isolated by user.
        env = {**os.environ, **config.env, "DSH_HOME": str(data_dir)}
        extra_args: list[str] = []
        if self.bridge:
            launch_env, extra_args = await self.bridge.launch(
                session.user_id, data_dir, await self.preferences(session.user_id)
            )
            env.update(launch_env)
        client = StreamingClient(runner_name="dsh", runner_config=config, cwd=session.cwd)
        stack = AsyncExitStack()
        try:
            conn, process = await stack.enter_async_context(
                spawn_agent_process(
                    client,  # type: ignore[arg-type]
                    config.command,
                    *config.args,
                    *extra_args,
                    cwd=session.cwd,
                    env=env,
                    transport_kwargs={"limit": config.stdio_buffer_limit_bytes},
                    observers=[client.observe],
                )
            )

            async def drain_stderr() -> None:
                if process.stderr is not None:
                    while await process.stderr.read(8192):
                        pass

            session.stderr_task = asyncio.create_task(drain_stderr())
            async with asyncio.timeout(30):
                from octop import __version__

                initialized = await conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_info=Implementation(name="Singu-Xfriend", version=__version__),
                )
                if initialized.protocol_version != PROTOCOL_VERSION:
                    raise ValueError("Unsupported ACP protocol version")
                mcp_servers: list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio] = [
                    HttpMcpServer.model_validate(item)
                    if item.get("type") == "http"
                    else McpServerStdio.model_validate(item)
                    for item in [
                        *session.mcp_extensions,
                        *(
                            [self.bridge.issue(session.id, session.user_id)]
                            if self.bridge
                            and (await self.preferences(session.user_id))["connectors"]
                            else []
                        ),
                    ]
                ]
                if session.acp_id:
                    # Fail explicitly if resume fails; never silently discard context.
                    await conn.resume_session(
                        session_id=session.acp_id, cwd=session.cwd, mcp_servers=mcp_servers
                    )
                else:
                    created = await conn.new_session(cwd=session.cwd, mcp_servers=mcp_servers)
                    session.acp_id = created.session_id
                    await self._save(session)
        except BaseException:
            await stack.aclose()
            if session.stderr_task is not None:
                session.stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session.stderr_task
            raise
        session.client, session.conn, session.process, session.stack = client, conn, process, stack

    async def prompt(self, session: Session, text: str) -> None:
        from octop.infra.licensing.service import require_product_license

        await require_product_license(self.home)
        if self.bridge:
            self.bridge.issue(session.id, session.user_id)
        async with session.lock:
            if session.task is not None and not session.task.done():
                raise ValueError("Session is busy")
            if not text.strip():
                raise ValueError("Prompt is empty")
            session.state, session.error = "running", None
            session.messages.append({"role": "user", "text": text})
            await self._save(session)
            session.task = asyncio.create_task(self._run(session, text))

    async def _run(self, session: Session, text: str) -> None:
        from acp import text_block

        assistant: dict[str, Any] = {"role": "assistant", "text": "", "tools": []}

        async def receive(event: dict[str, Any], flush: bool) -> None:
            if event.get("type") == "text":
                assistant["text"] += event["text"]
                await self._save(session)
            elif event.get("type") == "permission_request":
                session.permission = event
                session.state = "permission"
            else:
                assistant["tools"].append(event)

        try:
            await self._open(session)
            session.messages.append(assistant)
            assert session.client is not None
            session.client.start_prompt(receive)
            response = await session.conn.prompt(
                session_id=session.acp_id, prompt=[text_block(text)]
            )
            # Responses bypass the ACP notification queue; drain committed output first.
            async with asyncio.timeout(30):
                await session.client.drained.wait()
            assistant["stop_reason"] = response.stop_reason
            session.state = "cancelled" if response.stop_reason == "cancelled" else "idle"
        except asyncio.CancelledError:
            session.state = "interrupted"
            raise
        except Exception as exc:
            session.state, session.error = "failed", str(exc)
        finally:
            session.permission = None
            await self._save(session)

    async def approve(self, session: Session, option_id: str) -> None:
        if session.client is None or session.state != "permission":
            raise ValueError("No pending permission")
        session.client.resolve_permission(option_id)
        session.permission, session.state = None, "running"

    async def cancel(self, session: Session) -> None:
        if session.task is None or session.task.done():
            return
        if session.conn is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(5):
                    await session.conn.cancel(session_id=session.acp_id)
                    await asyncio.shield(session.task)
        if not session.task.done():
            session.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.task
            await self._close(session)

    async def _close(self, session: Session) -> None:
        if session.stack is not None:
            if session.conn is not None:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(5):
                        await session.conn.close_session(session_id=session.acp_id)
            await session.stack.aclose()
            session.stack = None
        if session.stderr_task is not None:
            session.stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.stderr_task
            session.stderr_task = None
        session.conn = session.process = session.client = None

    async def shutdown(self) -> None:
        for session in list(self.sessions.values()):
            await self.cancel(session)
            await self._close(session)

    async def close(self, session: Session) -> None:
        """Close one owned session without deleting its history or checkpoint."""
        await self.cancel(session)
        await self._close(session)
