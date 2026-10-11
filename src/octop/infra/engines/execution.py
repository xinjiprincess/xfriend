"""Expert-engine bindings and durable, sequential mixed-engine tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from octop.i18n import tr
from octop.infra.db.services import RepoBundle
from octop.infra.engines.contracts import ExecutionContext, ExecutionEngine
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.hitl.format import is_ask_action_requests
from octop.infra.users.identity import User
from octop.infra.utils.async_io import finish_io

ACTIVE_STATES = {"queued", "running", "permission"}
logger = logging.getLogger(__name__)


class ExecutionService:
    @property
    def has_active_tasks(self) -> bool:
        return any(not task.done() for task in tuple(self._running.values()))

    def __init__(self, home: Path, repos: RepoBundle, engines: dict[str, ExecutionEngine]) -> None:
        self.root = home / "execution"
        self.repos = repos
        self.engines = engines
        self._tasks: dict[tuple[int, str], dict[str, Any]] = {}
        self._running: dict[tuple[int, str], asyncio.Task[None]] = {}
        self._permissions: dict[tuple[int, str], asyncio.Future[dict[str, Any]]] = {}
        self._contexts: dict[tuple[int, str], tuple[ExecutionEngine, ExecutionContext]] = {}
        self._run_lock = asyncio.Lock()
        self._bindings_lock = asyncio.Lock()
        self._save_locks: dict[tuple[int, str], asyncio.Lock] = {}

    def _path(self, user_id: int, task_id: str) -> Path:
        try:
            if str(uuid.UUID(task_id)) != task_id:
                raise ValueError
        except ValueError as exc:
            raise OctopError(ErrorCode.NOT_FOUND, "Execution task not found") from exc
        return self.root / str(user_id) / f"{task_id}.json"

    async def _save(self, task: dict[str, Any]) -> None:
        key = (task["user_id"], task["id"])
        async with self._save_locks.setdefault(key, asyncio.Lock()):
            await self._write(task)

    async def _write(self, task: dict[str, Any]) -> None:
        path = self._path(task["user_id"], task["id"])
        text = json.dumps(task, ensure_ascii=False)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(text, "utf-8")
            temporary.replace(path)

        await finish_io(write)

    def _expert(self, user: User, expert_id: str) -> Any:
        row = self.repos.agent_repo.get(expert_id)
        if row is None or not row.enabled:
            raise OctopError(ErrorCode.NOT_FOUND, "Expert not found")
        if not (user.is_admin or row.user_id == user.id or row.is_shared):
            raise OctopError(ErrorCode.NOT_FOUND, "Expert not found")
        if row.kind == "team":
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Select team members as sequential steps")
        return row

    def _bindings(self, user_id: int) -> dict[str, Any]:
        raw = self.repos.settings_repo.get(f"engine_bindings:{user_id}")
        return dict(json.loads(raw)) if raw else {}

    async def experts(self, user: User) -> list[dict[str, Any]]:
        def read() -> list[dict[str, Any]]:
            bindings = self._bindings(user.id)
            return [
                {
                    "id": row.agent_id,
                    "name": row.name,
                    **bindings.get(row.agent_id, {"engine": "native", "workspace": ""}),
                    "capabilities": sorted(
                        self.engines[
                            bindings.get(row.agent_id, {}).get("engine", "native")
                        ].capabilities
                    ),
                }
                for row in self.repos.agent_repo.list_all(include_disabled=False)
                if row.kind != "team" and (user.is_admin or row.user_id == user.id or row.is_shared)
            ]

        return await asyncio.get_running_loop().run_in_executor(None, read)

    async def bind(self, user: User, expert_id: str, engine: str, workspace: str) -> dict[str, Any]:
        # DSH retains its existing administrator-only boundary.
        if engine not in self.engines or (engine == "dsh" and not user.is_admin):
            raise OctopError(ErrorCode.FORBIDDEN, "Engine is not available to this user")

        def save() -> dict[str, Any]:
            row = self._expert(user, expert_id)
            config = json.loads(row.config_json or "{}")
            if engine == "dsh":
                backend = config.get("backend") or {}
                if backend.get("type", "local") not in {"local", "filesystem", "local_shell"}:
                    raise OctopError(ErrorCode.SLASH_BAD_ARGS, "DSH requires a local workspace")
                directory = Path(workspace).expanduser()
                if (
                    not directory.is_absolute()
                    or not directory.is_dir()
                    or str(directory).startswith(("\\\\", "//"))
                ):
                    raise OctopError(
                        ErrorCode.SLASH_BAD_ARGS, "Select an existing absolute local directory"
                    )
                workspace_value = str(directory.resolve())
            else:
                workspace_value = ""
            binding = {"engine": engine, "workspace": workspace_value}
            bindings = self._bindings(user.id)
            bindings[expert_id] = binding
            self.repos.settings_repo.set(f"engine_bindings:{user.id}", json.dumps(bindings))
            return {"id": expert_id, "name": row.name, **binding}

        # No await separates the read-modify-write inside this synchronous callback.
        # Database writes serialize; callers on this event loop use the binding lock.
        async with self._bindings_lock:
            return await asyncio.get_running_loop().run_in_executor(None, save)

    async def create(self, user: User, expert_ids: list[str], text: str) -> dict[str, Any]:
        if not text.strip() or not 1 <= len(expert_ids) <= 4:
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Choose one to four expert steps")

        def specs() -> list[dict[str, Any]]:
            bindings = self._bindings(user.id)
            result = []
            for expert_id in expert_ids:
                row = self._expert(user, expert_id)
                binding = bindings.get(expert_id, {"engine": "native", "workspace": ""})
                if binding["engine"] == "dsh" and not user.is_admin:
                    raise OctopError(ErrorCode.FORBIDDEN, "DSH requires administrator access")
                result.append(
                    {
                        "expert_id": expert_id,
                        "name": row.name,
                        **binding,
                        "session_id": str(uuid.uuid4()),
                        "system_prompt": row.system_prompt or "",
                    }
                )
            return result

        steps = await asyncio.get_running_loop().run_in_executor(None, specs)
        task: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "user_id": user.id,
            "state": "queued",
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
            "steps": steps,
            "messages": [{"role": "user", "content": text}],
            "events": [],
            "permission": None,
            "step": 0,
            "error": None,
        }
        key = (user.id, task["id"])
        self._tasks[key] = task
        await self._save(task)
        self._running[key] = asyncio.create_task(self._execute(user, task, text))
        return task

    async def get(self, user_id: int, task_id: str) -> dict[str, Any]:
        key = (user_id, task_id)
        path = self._path(user_id, task_id)
        if key not in self._tasks:
            try:
                raw = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: json.loads(path.read_text("utf-8"))
                )
            except FileNotFoundError as exc:
                raise OctopError(ErrorCode.NOT_FOUND, "Execution task not found") from exc
            if raw["state"] in ACTIVE_STATES:
                raw.update(state="interrupted", permission=None)
            self._tasks.setdefault(key, raw)
        return self._tasks[key]

    async def list_tasks(self, user_id: int) -> list[dict[str, Any]]:
        paths = await asyncio.get_running_loop().run_in_executor(
            None, lambda: sorted((self.root / str(user_id)).glob("*.json"))
        )
        return [await self.get(user_id, path.stem) for path in paths]

    async def document_messages(self, user_id: int, task_id: str) -> list[dict[str, Any]]:
        task = await self.get(user_id, task_id)
        messages = []
        for original in task["messages"]:
            message = dict(original)
            if message["role"] == "assistant":
                step = task["steps"][message["step"]]
                provenance = f"[{step['name']} · {step['engine']} · {step.get('engine_version', 'unversioned')}]"
                message["content"] = provenance + "\n\n" + message["content"]
            messages.append(message)
        return messages

    async def followup(self, user: User, task_id: str, text: str) -> dict[str, Any]:
        task = await self.get(user.id, task_id)
        key = (user.id, task_id)
        if sum(len(message["content"]) for message in task["messages"]) > 4_000_000:
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS, "Start a new task after the transcript limit"
            )
        if task["state"] != "succeeded" or key in self._running or not text.strip():
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS, "Only completed tasks can accept a follow-up"
            )
        task.update(state="queued", error=None)
        task["messages"].append({"role": "user", "content": text})
        self._running[key] = asyncio.create_task(self._execute(user, task, text))
        return task

    async def assistant_preferences(
        self, user: User, expert_ids: list[str] | None = None, word: str | None = None
    ) -> dict[str, Any]:
        if word is not None and not 2 <= len(word.strip()) <= 20:
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS, "Wake word must contain two to twenty characters"
            )

        def access() -> dict[str, Any]:
            key = f"assistant_execution:{user.id}"
            saved = json.loads(self.repos.settings_repo.get(key) or "{}")
            if expert_ids is not None:
                if len(expert_ids) > 4:
                    raise OctopError(ErrorCode.SLASH_BAD_ARGS, "At most four expert steps")
                for expert_id in expert_ids:
                    self._expert(user, expert_id)
                saved["expert_ids"] = expert_ids
            if word is not None:
                saved["wake_word"] = word.strip()
            result = {
                "expert_ids": saved.get("expert_ids", []),
                "wake_word": saved.get("wake_word", "小星"),
            }
            if expert_ids is not None or word is not None:
                self.repos.settings_repo.set(key, json.dumps(result, ensure_ascii=False))
            return result

        async with self._bindings_lock:
            return await asyncio.get_running_loop().run_in_executor(None, access)

    async def _event(self, task: dict[str, Any], payload: dict[str, Any]) -> None:
        payload = TypeAdapter(dict[str, Any]).dump_python(payload, mode="json", fallback=str)
        if len(json.dumps(payload, ensure_ascii=False)) > 256_000:
            raise ValueError("TASK_EVENT_SIZE_LIMIT")
        if len(task["events"]) >= 10000:
            raise ValueError("TASK_EVENT_LIMIT")
        sequence = len(task["events"]) + 1
        task["events"].append(
            {
                **payload,
                "id": f"{task['id']}:{sequence}",
                "sequence": sequence,
                "task_id": task["id"],
                "step": task["step"],
            }
        )
        task["updated_at"] = int(time.time())
        # Persist state boundaries and batches, rather than rewriting on every token.
        if payload.get("type") != "token" or sequence % 20 == 0:
            await self._save(task)

    async def _execute(self, user: User, task: dict[str, Any], text: str) -> None:
        key = (user.id, task["id"])
        try:
            async with self._run_lock, asyncio.timeout(600):
                task["state"] = "running"
                await self._event(task, {"type": "started"})
                previous = text
                for index, step in enumerate(task["steps"]):
                    # Re-check access after queueing; disabled/deleted experts cannot run.
                    await asyncio.get_running_loop().run_in_executor(
                        None, self._expert, user, step["expert_id"]
                    )
                    task["step"] = index
                    engine = self.engines[step["engine"]]
                    context = ExecutionContext(
                        user.id,
                        step["expert_id"],
                        step["session_id"],
                        step["workspace"],
                        step.get("engine_session_id"),
                        is_admin=user.is_admin,
                    )
                    self._contexts[key] = (engine, context)
                    await self._event(
                        task,
                        {
                            "type": "step_started",
                            "engine": engine.id,
                            "expert_id": context.expert_id,
                        },
                    )

                    async def emit(
                        event: dict[str, Any], current_step: dict[str, Any] = step
                    ) -> None:
                        if event.get("type") == "session":
                            current_step["engine_session_id"] = event["session_id"]
                        if event.get("engine_version"):
                            current_step["engine_version"] = event["engine_version"]
                        await self._event(task, event)

                    async def permission(request: dict[str, Any]) -> dict[str, Any]:
                        future: asyncio.Future[dict[str, Any]] = (
                            asyncio.get_running_loop().create_future()
                        )
                        self._permissions[key] = future
                        task.update(
                            state="permission",
                            permission={"id": str(uuid.uuid4()), "request": request},
                        )
                        await self._event(task, {"type": "permission"})
                        try:
                            return await future
                        finally:
                            self._permissions.pop(key, None)
                            task.update(state="running", permission=None)

                    prompt = (
                        text
                        if index == 0
                        else tr("execution.handoff", user.locale, text=text, previous=previous)
                    )
                    if engine.id == "dsh":
                        prompt = tr(
                            "execution.expert",
                            user.locale,
                            name=step["name"],
                            persona=step.get("system_prompt", ""),
                            text=prompt,
                        )
                    try:
                        previous = await engine.execute(context, prompt, emit, permission)
                        if len(previous) > 2_000_000:
                            raise ValueError("TASK_OUTPUT_LIMIT")
                    except BaseException:
                        await engine.cancel(context)
                        raise
                    finally:
                        await engine.close(context)
                    task["messages"].append(
                        {
                            "role": "assistant",
                            "content": previous,
                            "expert_id": step["expert_id"],
                            "engine": engine.id,
                            "step": index,
                        }
                    )
                    await self._event(task, {"type": "step_completed"})
                task["state"] = "succeeded"
        except asyncio.CancelledError:
            task["state"] = "cancelled"
        except TimeoutError:
            task.update(state="failed", error="TASK_TIMEOUT")
        except Exception as exc:
            logger.error(
                "Execution task %s failed (%s)\n%s",
                task["id"],
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            task.update(
                state="failed",
                error=exc.code.value if isinstance(exc, OctopError) else "ENGINE_EXECUTION_FAILED",
            )
        finally:
            task["permission"] = None
            self._contexts.pop(key, None)
            try:
                if len(task["events"]) < 10000:
                    await self._event(task, {"type": "finished", "state": task["state"]})
                else:
                    await self._save(task)
            finally:
                self._running.pop(key, None)

    async def answer(
        self, user_id: int, task_id: str, permission_id: str, answer: dict[str, Any]
    ) -> dict[str, Any]:
        task = await self.get(user_id, task_id)
        pending = task.get("permission")
        future = self._permissions.get((user_id, task_id))
        if not pending or pending["id"] != permission_id or future is None or future.done():
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Permission request is no longer active")
        engine = task["steps"][task["step"]]["engine"]
        if engine == "native":
            actions = pending["request"].get("request", {}).get("action_requests", [])
            if is_ask_action_requests(actions):
                if (
                    answer.get("decision") != "respond"
                    or not str(answer.get("message", "")).strip()
                ):
                    raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Question answer required")
            elif answer.get("decision") not in {"approve", "reject"}:
                raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Invalid permission decision")
        if engine == "dsh":
            options = pending["request"].get("options") or []
            if not any(option.get("option_id") == answer.get("option_id") for option in options):
                raise OctopError(ErrorCode.SLASH_BAD_ARGS, "Invalid permission option")
        future.set_result(answer)
        return task

    async def cancel(self, user_id: int, task_id: str) -> dict[str, Any]:
        task = await self.get(user_id, task_id)
        running = self._running.get((user_id, task_id))
        if running and not running.done():
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                # A queued coroutine may be cancelled before its first instruction.
                task.update(state="cancelled", permission=None)
                self._running.pop((user_id, task_id), None)
                await self._save(task)
        return task

    async def shutdown(self) -> None:
        for user_id, task_id in list(self._running):
            await self.cancel(user_id, task_id)
