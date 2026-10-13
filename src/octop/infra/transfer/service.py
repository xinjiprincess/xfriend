"""Portable configuration orchestration, ownership checks and durable import journals."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from octop.i18n import tr
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.transfer.archive import MAX_ARCHIVE, InvalidPackage, decode, encode
from octop.infra.transfer.models import (
    Choice,
    Expert,
    ExportSelection,
    ImportPlan,
    ModelRef,
    Package,
    Provider,
    RuntimePreferences,
)
from octop.infra.users.identity import User
from octop.infra.users.permissions import user_has_permission

PROMPTS = ("SOUL.md", "IDENTITY.md", "AGENTS.md", "TOOLS.md", "HEARTBEAT.md")
MODEL_FIELDS = frozenset(
    {
        "id",
        "name",
        "context",
        "context_window",
        "max_tokens",
        "max_output_tokens",
        "input",
        "reasoning",
        "cost",
        "enabled",
        "embedding",
        "image",
        "video",
        "audio",
    }
)
EXTRA_FIELDS = frozenset(
    {
        "model",
        "asr_model",
        "tts_model",
        "voice",
        "language",
        "speed",
        "response_format",
        "api_version",
        "temperature",
        "top_p",
        "max_tokens",
        "sample_rate",
        "stream",
    }
)


def _object(raw: str | None) -> dict[str, Any]:
    result = json.loads(raw or "{}")
    return result if isinstance(result, dict) else {}


def _ids(raw: str | None) -> list[str]:
    result = json.loads(raw or "[]")
    return [str(v) for v in result] if isinstance(result, list) else []


def _safe_extra(value: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in value.items()
        if k in EXTRA_FIELDS and isinstance(v, str | int | float | bool)
    }


def _safe_models(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    models = [{k: v for k, v in model.items() if k in MODEL_FIELDS} for model in value]
    # Drop arbitrary nested dictionaries rather than inadvertently carrying headers/auth.
    for model in models:
        for key in list(model):
            if isinstance(model[key], dict):
                if key == "cost":
                    model[key] = {
                        k: v
                        for k, v in model[key].items()
                        if k in {"input", "output", "cache_read", "cache_write"}
                        and isinstance(v, int | float)
                    }
                else:
                    del model[key]
    return models


class PortablePackService:
    def __init__(self, services: SharedServices, manager: Any, bind_engine: Any = None) -> None:
        self.services = services
        self.manager = manager
        self.bind_engine = bind_engine
        self.root = services.paths.root / "transfer"
        self._lock = asyncio.Lock()
        self._crypto_slots = asyncio.Semaphore(2)
        self._tasks: set[asyncio.Task[None]] = set()

    def error(self, user: User, key: str = "invalid") -> OctopError:
        return OctopError(ErrorCode.SLASH_BAD_ARGS, tr(f"transfer.{key}", user.locale))

    def _dir(self, user: User, identity: str) -> Path:
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise self.error(user)
        return self.root / str(user.id) / identity

    def _key(self, user: User, identity: str) -> str:
        self._dir(user, identity)
        return f"transfer:{user.id}:{identity}"

    def _read(self, user: User, identity: str) -> dict[str, Any]:
        raw = self.services.settings_repo.get(self._key(user, identity))
        if not raw:
            raise OctopError.localized(ErrorCode.NOT_FOUND, user.locale)
        return _object(raw)

    def _save(self, user: User, identity: str, state: dict[str, Any]) -> None:
        self.services.settings_repo.set(self._key(user, identity), json.dumps(state))

    async def _io_save(self, user: User, identity: str, state: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save, user, identity, state)

    def _own(self, user: User, identity: str) -> Any:
        row = self.services.agent_repo.get(identity)
        if row is None or (row.user_id != user.id and not user.is_admin):
            raise OctopError.localized(ErrorCode.FORBIDDEN, user.locale)
        return row

    def _manage_models(self, user: User) -> None:
        if not user_has_permission(user, "providers"):
            raise OctopError.localized(ErrorCode.FORBIDDEN, user.locale)

    async def recover(self, user: User) -> None:
        """Only scan this user's tiny journal namespace, on visiting transfer, not login."""
        async with self._lock:
            rows = await asyncio.to_thread(
                self.services.settings_repo.list_prefix, f"transfer:{user.id}:"
            )
            for key, raw in rows:
                identity = key.rsplit(":", 1)[1]
                state = _object(raw)
                if (
                    state.get("state") in {"completed", "failed", "cancelled"}
                    and isinstance(state.get("created_at"), int | float)
                    and time.time() - state["created_at"] > 7 * 86400
                ):
                    await asyncio.to_thread(shutil.rmtree, self._dir(user, identity), True)
                    await asyncio.to_thread(self.services.settings_repo.delete, key)
                    continue
                if state.get("state") == "committing":
                    await self._rollback(user, identity, state)
                elif state.get("state") == "publishing":
                    await self._publish(user, identity, state)

    async def inventory(self, user: User) -> dict[str, Any]:
        await self.recover(user)

        def read() -> dict[str, Any]:
            agents = [
                r
                for r in self.services.agent_repo.list_all(include_disabled=False)
                if r.user_id == user.id or user.is_admin
            ]
            return {
                "agents": [{"id": r.agent_id, "name": r.name, "kind": r.kind} for r in agents],
                "providers": [
                    {
                        "id": r.id,
                        "name": r.name,
                        "kind": r.kind,
                        "family": "text",
                        "models": [str(m.get("id", "")) for m in r.get_models()],
                    }
                    for r in self.services.provider_repo.list_all()
                ],
                "voice_providers": [
                    {"id": r.id, "name": r.name, "kind": r.kind, "family": "voice"}
                    for r in self.services.voice_provider_repo.list_all()
                ],
                "can_manage_models": user_has_permission(user, "providers"),
            }

        return await asyncio.to_thread(read)

    async def start_export(self, user: User, selection: ExportSelection) -> dict[str, Any]:
        if (
            not selection.agent_ids
            and not selection.provider_ids
            and not selection.voice_provider_ids
        ):
            raise self.error(user, "empty")
        if selection.include_secrets:
            self._manage_models(user)
            if not selection.password:
                raise self.error(user, "password")
        identity = uuid.uuid4().hex
        state = {"kind": "export", "state": "running", "created_at": time.time()}
        await self._io_save(user, identity, state)
        task = asyncio.create_task(self._export(user, identity, selection, state))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"id": identity, **state}

    async def _export(
        self, user: User, identity: str, selection: ExportSelection, state: dict[str, Any]
    ) -> None:
        try:
            package, files, secrets = await self._snapshot(user, selection)
            async with self._crypto_slots:
                data = await asyncio.to_thread(encode, package, files, secrets, selection.password)
            await asyncio.to_thread(
                self._write_file, self._dir(user, identity) / "package.xfpack", data
            )
            review = []
            for expert in package.experts:
                for relative, path in expert.files.items():
                    content = files[path]
                    text = content.decode("utf-8", errors="replace")
                    review.append(
                        {
                            "expert": expert.name,
                            "path": relative,
                            "size": len(content),
                            "sample": text[:2000],
                            "possible_secret": bool(
                                re.search(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]", text)
                            ),
                        }
                    )
            state.update(state="completed", warnings=package.warnings, preview=review)
        except Exception:
            state.update(state="failed", message=tr("transfer.export_failed", user.locale))
        await self._io_save(user, identity, state)

    @staticmethod
    def _write_file(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    async def _snapshot(
        self, user: User, selection: ExportSelection
    ) -> tuple[Package, dict[str, bytes], dict[str, str]]:
        from octop import __version__
        from octop.infra.agents.experts.avatar import read_workspace_avatar
        from octop.infra.skills.skill_transfer import read_workspace_skill_files

        rows: dict[str, Any] = {}
        for agent_id in selection.agent_ids:
            row = await asyncio.to_thread(self._own, user, agent_id)
            rows[agent_id] = row
            if row.kind == "team":
                members = await asyncio.to_thread(self.manager.teams.member_ids, agent_id)
                for member in members:
                    rows[member] = await asyncio.to_thread(self._own, user, member)
        files: dict[str, bytes] = {}
        secrets: dict[str, str] = {}
        providers: dict[str, Provider] = {}
        warnings = [tr("transfer.review_files", user.locale)]

        def add_provider(row: Any, family: str) -> str:
            ref = f"{family}:{row.id}"
            if ref not in providers:
                credential = row.api_key if selection.include_secrets else None
                if any(s in row.kind.lower() for s in ("codex", "oauth", "opencode")):
                    credential = None
                models = _safe_models(row.get_models()) if family == "text" else []
                providers[ref] = Provider(
                    ref=ref,
                    name=row.name,
                    family=cast(Literal["text", "voice"], family),
                    kind=row.kind,
                    capability=getattr(row, "capability", "both"),
                    base_url=row.base_url,
                    models=models,
                    extra=_safe_extra(_object(row.extra_json)),
                    has_secret=bool(credential),
                )
                if credential:
                    secrets[ref] = credential
            return ref

        for family, values, repo in (
            ("text", selection.provider_ids, self.services.provider_repo),
            ("voice", selection.voice_provider_ids, self.services.voice_provider_repo),
        ):
            if values:
                self._manage_models(user)
            for value in values:
                provider = await asyncio.to_thread(repo.get, value)
                if provider is None:
                    raise self.error(user)
                add_provider(provider, family)
        raw_bindings = await asyncio.to_thread(
            self.services.settings_repo.get, f"engine_bindings:{user.id}"
        )
        bindings = _object(raw_bindings)
        experts: list[Expert] = []
        for row in rows.values():
            config = _object(row.config_json)
            model = None
            model_ref = row.default_model or self.manager.resolve_fallback_model_ref()
            if model_ref and "/" in model_ref:
                name, _, model_id = model_ref.partition("/")
                provider = await asyncio.to_thread(self.services.provider_repo.get_by_name, name)
                if provider is not None:
                    # Reading shared model descriptions is allowed; exporting keys still requires permission.
                    model = ModelRef(provider_ref=add_provider(provider, "text"), model_id=model_id)
            expert = Expert(
                ref=f"expert:{row.agent_id}",
                name=row.name,
                kind=row.kind,
                description=row.description,
                system_prompt=row.system_prompt,
                welcome_message=row.welcome_message,
                persona_mbti=row.persona_mbti,
                color=row.color,
                icon_name=row.icon_name,
                bundled_icon=row.icon_url
                if row.icon_url and row.icon_url.startswith("/experts/avatars/")
                else None,
                model=model,
                engine=bindings.get(row.agent_id, {}).get("engine", "native"),
                preferences=RuntimePreferences.model_validate(
                    {k: v for k, v in config.items() if k in RuntimePreferences.model_fields}
                ),
                dependencies=[
                    *(f"knowledge:{v}" for v in _ids(row.knowledge_base_ids)),
                    *(f"mcp:{v}" for v in _ids(row.mcp_servers)),
                    *(f"plugin:{v}" for v in config.get("plugins", []) if isinstance(v, str)),
                ],
            )
            if row.kind == "team":
                members = await asyncio.to_thread(self.manager.teams.member_ids, row.agent_id)
                expert.member_refs = [f"expert:{v}" for v in members]
                from octop.infra.agents.teams.configuration import member_models

                for member_id, reference in member_models(row).items():
                    if member_id not in members:
                        continue
                    provider_name, _, model_id = reference.partition("/")
                    provider = self.services.provider_repo.get_by_name(provider_name)
                    if provider is None:
                        raise self.error(user, "model_missing")
                    expert.member_models[f"expert:{member_id}"] = ModelRef(
                        provider_ref=add_provider(provider, "text"), model_id=model_id
                    )
            workspace = self.manager.workspace_for_agent(row.agent_id)
            if workspace is None:
                raise self.error(user)
            for name in PROMPTS:
                blob = await workspace.adownload_bytes(name)
                if blob is not None:
                    path = f"assets/{row.agent_id}/{name}"
                    files[path] = blob
                    expert.files[name] = path
            avatar = await read_workspace_avatar(workspace)
            if avatar:
                expert.avatar_file = f"assets/{row.agent_id}/avatar"
                files[expert.avatar_file] = avatar[0]
            if selection.include_skills:
                result = await workspace.als("skills")
                for entry in getattr(result, "entries", []) or []:
                    if not getattr(entry, "is_dir", False):
                        continue
                    slug = (
                        str(getattr(entry, "path", ""))
                        .replace("\\", "/")
                        .rstrip("/")
                        .rsplit("/", 1)[-1]
                    )
                    for relative, content in await read_workspace_skill_files(workspace, slug):
                        name = f"skills/{slug}/{relative}"
                        path = f"assets/{row.agent_id}/{name}"
                        expert.files[name] = path
                        files[path] = content
            # Do not silently accept source edits during file copying.
            fresh = await asyncio.to_thread(self._own, user, row.agent_id)
            if fresh.config_json != row.config_json or fresh.updated_at != row.updated_at:
                raise self.error(user, "changed")
            experts.append(expert)
        package = Package(
            package_id=uuid.uuid4().hex,
            client_version=__version__,
            providers=list(providers.values()),
            experts=experts,
            warnings=warnings,
        )
        # Round-trip validation catches unsafe/oversized resource paths before handing out a package.
        return package, files, secrets

    async def job(self, user: User, identity: str) -> dict[str, Any]:
        state = await asyncio.to_thread(self._read, user, identity)
        return {
            "id": identity,
            **{
                k: state[k]
                for k in ("kind", "state", "created_at", "warnings", "message", "result", "preview")
                if k in state
            },
        }

    async def download(self, user: User, identity: str) -> Path:
        state = await asyncio.to_thread(self._read, user, identity)
        if state.get("kind") != "export" or state.get("state") != "completed":
            raise self.error(user)
        return self._dir(user, identity) / "package.xfpack"

    async def inspect(self, user: User, data: bytes, password: str) -> dict[str, Any]:
        await self.recover(user)
        package, _files, _secrets = await self._decode(user, data, password)
        identity = uuid.uuid4().hex
        await asyncio.to_thread(
            self._write_file, self._dir(user, identity) / "package.xfpack", data
        )
        await self._io_save(
            user,
            identity,
            {
                "kind": "import",
                "state": "awaiting_mapping",
                "created_at": time.time(),
                "digest": hashlib.sha256(data).hexdigest(),
            },
        )
        return {"id": identity, "package": package.model_dump(exclude={"checksums"})}

    async def _decode(
        self, user: User, data: bytes, password: str
    ) -> tuple[Package, dict[str, bytes], dict[str, str]]:
        try:
            async with self._crypto_slots:
                package, files, secrets = await asyncio.to_thread(decode, data, password)
            for p in package.providers:
                if p.extra != _safe_extra(p.extra) or p.models != _safe_models(p.models):
                    raise InvalidPackage
            return package, files, secrets
        except Exception:
            raise self.error(user) from None

    async def plan(self, user: User, identity: str, plan: ImportPlan) -> dict[str, Any]:
        async with self._lock:
            state = await asyncio.to_thread(self._read, user, identity)
            if state.get("kind") != "import" or state.get("state") != "awaiting_mapping":
                raise self.error(user)
            revision = uuid.uuid4().hex
            state.update(plan=plan.model_dump(), revision=revision)
            await self._io_save(user, identity, state)
            return {"id": identity, "revision": revision}

    async def commit(
        self, user: User, identity: str, revision: str, password: str
    ) -> dict[str, Any]:
        async with self._lock:
            state = await asyncio.to_thread(self._read, user, identity)
            if state.get("revision") != revision:
                raise self.error(user, "changed")
            if state.get("state") == "completed":
                return dict(state["result"])
            if state.get("state") == "publishing":
                await self._publish(user, identity, state)
                return dict(state["result"])
            if state.get("state") != "awaiting_mapping":
                raise self.error(user)
            path = self._dir(user, identity) / "package.xfpack"
            data = await asyncio.to_thread(path.read_bytes)
            if hashlib.sha256(data).hexdigest() != state["digest"] or len(data) > MAX_ARCHIVE:
                raise self.error(user)
            package, files, secrets = await self._decode(user, data, password)
            plan = ImportPlan.model_validate(state["plan"])
            choices = self._validate_plan(user, package, plan)
            state.update(state="committing", agents=[], providers=[])
            await self._io_save(user, identity, state)
            try:
                result = await self._apply(
                    user, identity, state, package, files, secrets, plan, choices
                )
                state.update(state="publishing", result=result)
                await self._io_save(user, identity, state)
                await self._publish(user, identity, state)
                return result
            except BaseException:
                await asyncio.shield(self._rollback(user, identity, state))
                raise

    def _validate_plan(self, user: User, package: Package, plan: ImportPlan) -> dict[str, Choice]:
        resources: list[Provider | Expert] = [*package.providers, *package.experts]
        refs = {r.ref for r in resources}
        if set(plan.choices) - refs:
            raise self.error(user)
        choices = {r.ref: plan.choices.get(r.ref, Choice()) for r in resources}
        for p in package.providers:
            choice = choices[p.ref]
            if choice.action == "create":
                self._manage_models(user)
            elif choice.action == "reuse":
                repo: Any = (
                    self.services.provider_repo
                    if p.family == "text"
                    else self.services.voice_provider_repo
                )
                try:
                    target = repo.get(int(choice.target or ""))
                except ValueError:
                    target = None
                if target is None or not target.enabled:
                    raise self.error(user, "changed")
                if p.family == "text":
                    model_ids = {str(m.get("id")) for m in target.get_models()}
                    required = {
                        model.model_id
                        for e in package.experts
                        for model in ([e.model] if e.model else []) + list(e.member_models.values())
                        if model.provider_ref == p.ref and choices[e.ref].action == "create"
                    }
                    if not required <= model_ids:
                        raise self.error(user, "model_missing")
        for expert in package.experts:
            choice = choices[expert.ref]
            if choice.action == "reuse":
                row = self._own(user, choice.target or "")
                if not row.enabled or row.kind != expert.kind:
                    raise self.error(user)
            if choice.action == "create" and expert.kind == "team":
                if any(choices[ref].action == "skip" for ref in expert.member_refs):
                    raise self.error(user, "members_missing")
                reused = [
                    choices[ref].target
                    for ref in expert.member_refs
                    if choices[ref].action == "reuse"
                ]
                if len(reused) != len(set(reused)):
                    raise self.error(user, "members_missing")
            if choice.action == "create" and expert.engine == "dsh" and not user.is_admin:
                raise OctopError.localized(ErrorCode.FORBIDDEN, user.locale)
        return choices

    async def _apply(
        self,
        user: User,
        identity: str,
        state: dict[str, Any],
        package: Package,
        files: dict[str, bytes],
        secrets: dict[str, str],
        plan: ImportPlan,
        choices: dict[str, Choice],
    ) -> dict[str, Any]:
        from octop.infra.agents.experts.avatar import write_workspace_avatar
        from octop.infra.agents.manager import AgentCreateSpec
        from octop.infra.agents.teams.service import TEAM_TEMPLATE_NAME

        mapping: dict[str, str] = {}
        pending: list[dict[str, str]] = []
        for p in package.providers:
            choice = choices[p.ref]
            if choice.action == "skip":
                continue
            repo: Any = (
                self.services.provider_repo
                if p.family == "text"
                else self.services.voice_provider_repo
            )
            if choice.action == "reuse":
                target = await asyncio.to_thread(repo.get, int(choice.target or ""))
                mapping[p.ref] = target.name
                continue
            suffix = hashlib.sha256(p.ref.encode()).hexdigest()[:8]
            name = f"{p.name[:65]} ({identity[:10]}-{suffix})"
            marker = f"xfriend-transfer:{identity}"
            state["providers"].append({"family": p.family, "name": name, "marker": marker})
            await self._io_save(user, identity, state)
            kwargs: dict[str, Any] = {
                "name": name,
                "kind": p.kind,
                "base_url": p.base_url,
                "api_key": secrets.get(p.ref),
                "extra_json": json.dumps(p.extra),
                "note": marker,
            }
            if p.family == "text":
                kwargs["models_json"] = json.dumps(p.models)
            else:
                kwargs["capability"] = p.capability
            await asyncio.to_thread(repo.create, **kwargs)
            mapping[p.ref] = name
            if not p.has_secret and p.kind not in {"ollama", "edge", "browser", "local"}:
                pending.append({"ref": p.ref, "reason": "credentials"})
        existing_names = {
            r.name for r in await asyncio.to_thread(self.services.agent_repo.list_by_user, user.id)
        }
        for expert in sorted(package.experts, key=lambda e: e.kind == "team"):
            choice = choices[expert.ref]
            if choice.action == "skip":
                continue
            if choice.action == "reuse":
                mapping[expert.ref] = str(choice.target)
                continue
            agent_id = "xf" + uuid.uuid4().hex[:14]
            name = expert.name
            name_suffix = 2
            while name in existing_names:
                name = f"{expert.name[:85]} ({name_suffix})"
                name_suffix += 1
            existing_names.add(name)
            state["agents"].append(agent_id)
            await self._io_save(user, identity, state)
            model = None
            if expert.model and expert.model.provider_ref in mapping:
                model = f"{mapping[expert.model.provider_ref]}/{expert.model.model_id}"
            dependencies = list(expert.dependencies)
            if model is None:
                dependencies.append("model")
            if expert.engine == "dsh":
                dependencies.append("dsh-workspace")
            if any(p.startswith("skills/") for p in expert.files) and not plan.trust_skills:
                dependencies.append("skills-approval")
            pending.extend({"ref": expert.ref, "reason": reason} for reason in dependencies)
            config = expert.preferences.model_dump(exclude_none=True)
            if expert.kind == "team":
                overrides = {}
                for ref, selected_model in expert.member_models.items():
                    if selected_model.provider_ref in mapping:
                        overrides[mapping[ref]] = (
                            f"{mapping[selected_model.provider_ref]}/{selected_model.model_id}"
                        )
                    else:
                        raise self.error(user, "model_missing")
                config["team_member_models"] = overrides
            config.update(
                transfer_import=identity,
                transfer_pending=dependencies,
                transfer_engine=expert.engine,
            )

            async def initialize(row: Any, workspace: Any, definition: Expert = expert) -> None:
                for relative, file_ref in definition.files.items():
                    dest = relative
                    if relative.startswith("skills/") and not plan.trust_skills:
                        dest = "pending-import/" + relative
                    # Team orchestration rules must come from the target's trusted team template.
                    if definition.kind == "team" and relative in {"AGENTS.md", "SOUL.md"}:
                        continue
                    await workspace.aupload_bytes(dest, files[file_ref])
                if definition.avatar_file:
                    await write_workspace_avatar(workspace, files[definition.avatar_file])
                    self.services.agent_repo.update_config(
                        row.agent_id, icon_url=f"/api/agents/{row.agent_id}/avatar"
                    )

            spec = AgentCreateSpec(
                agent_id=agent_id,
                user_id=user.id,
                name=name,
                description=expert.description,
                kind=expert.kind,
                default_model=model,
                system_prompt=expert.system_prompt,
                persona_mbti=expert.persona_mbti,
                color=expert.color,
                icon_name=expert.icon_name,
                icon_url=expert.bundled_icon,
                welcome_message=expert.welcome_message,
                template_name=TEAM_TEMPLATE_NAME if expert.kind == "team" else None,
                member_ids=[mapping[ref] for ref in expert.member_refs],
                config=config,
                mcp_servers=[],
                knowledge_base_ids=[],
                skill_package_ids=[],
            )
            await self.manager.create(spec, start_runtime=False, workspace_initializer=initialize)
            mapping[expert.ref] = agent_id
        return {
            "mapping": mapping,
            "pending": pending,
            "created_agents": list(state["agents"]),
            "created_providers": len(state["providers"]),
            "state": "configuration_imported",
        }

    async def _publish(self, user: User, identity: str, state: dict[str, Any]) -> None:
        await self.manager.refresh_provider_catalog()
        for agent_id in state.get("agents", []):
            row = await asyncio.to_thread(self.services.agent_repo.get, agent_id)
            if row is None or _object(row.config_json).get("transfer_import") != identity:
                raise self.error(user, "changed")
            await asyncio.to_thread(self.services.agent_repo.set_enabled, agent_id, True)
            config = _object(row.config_json)
            if config.get("transfer_engine") == "dsh" and self.bind_engine is not None:
                workspace = str(self.manager.resolve_workspace_dir(agent_id))
                await self.bind_engine(user, agent_id, "dsh", workspace)
        state["state"] = "completed"
        await self._io_save(user, identity, state)
        await asyncio.to_thread(self._unlink_package, user, identity)

    async def _rollback(self, user: User, identity: str, state: dict[str, Any]) -> None:
        for agent_id in reversed(state.get("agents", [])):
            row = await asyncio.to_thread(self.services.agent_repo.get, agent_id)
            if row and _object(row.config_json).get("transfer_import") == identity:
                await self.manager.delete(agent_id)
        for provider in state.get("providers", []):
            repo: Any = (
                self.services.provider_repo
                if provider["family"] == "text"
                else self.services.voice_provider_repo
            )
            provider_row = await asyncio.to_thread(repo.get_by_name, provider["name"])
            if provider_row and provider_row.note == provider["marker"]:
                await asyncio.to_thread(repo.delete, provider_row.id)
        state.update(state="failed", message=tr("transfer.rolled_back", user.locale))
        await self._io_save(user, identity, state)

    def _unlink_package(self, user: User, identity: str) -> None:
        (self._dir(user, identity) / "package.xfpack").unlink(missing_ok=True)

    async def cancel(self, user: User, identity: str) -> None:
        async with self._lock:
            state = await asyncio.to_thread(self._read, user, identity)
            if state.get("state") != "awaiting_mapping":
                raise self.error(user)
            await asyncio.to_thread(self._unlink_package, user, identity)
            state["state"] = "cancelled"
            await self._io_save(user, identity, state)
