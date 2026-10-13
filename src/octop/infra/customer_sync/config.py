"""Portable configuration adapter. Retain xfpack data alongside phone fields."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid
from typing import Any

from octop.infra.customer_sync.models import Edit, Entity, Journal
from octop.infra.customer_sync.service import CustomerSyncService
from octop.infra.transfer.archive import encode, safe_path
from octop.infra.transfer.models import Expert, ExportSelection, Package, Provider
from octop.infra.transfer.service import PortablePackService
from octop.infra.users.identity import User


def stable_id(kind: str, value: str) -> str:
    return "desktop-" + kind + "-" + hashlib.sha256(value.encode()).hexdigest()[:24]


def entities_from_pack(
    package: Package, files: dict[str, bytes], secrets: dict[str, str]
) -> list[Entity]:
    result: list[Entity] = []
    for expert in package.experts:
        kind = expert.kind
        eid = stable_id(kind, expert.ref)
        skills: list[str] = []
        for relative, path in expert.files.items():
            if relative.startswith("skills/") and relative.endswith("/SKILL.md"):
                sid = stable_id("skill", expert.ref + ":" + relative)
                skills.append(sid)
                result.append(
                    Entity(
                        entity_type="skill",
                        entity_id=sid,
                        payload={
                            "id": sid,
                            "name": relative.split("/")[1],
                            "content": files[path].decode("utf-8"),
                        },
                    )
                )
        payload: dict[str, Any] = {
            "id": eid,
            "name": expert.name,
            "prompt": expert.system_prompt or "",
            "skills": skills,
            "knowledge": [],
            "members": [stable_id("expert", ref) for ref in expert.member_refs],
            "_xfpack": {
                "format_version": package.format_version,
                "client_version": package.client_version,
                "resource": expert.model_dump(mode="json"),
                "providers": [
                    p.model_dump(mode="json", exclude={"has_secret"}) for p in package.providers
                ],
                "files": {
                    p: base64.b64encode(files[p]).decode()
                    for p in [
                        *expert.files.values(),
                        *([expert.avatar_file] if expert.avatar_file else []),
                    ]
                },
            },
        }
        result.append(Entity(entity_type=kind, entity_id=eid, payload=payload))
    for provider in package.providers:
        if provider.family != "text":
            continue
        for model in provider.models:
            model_id = str(model.get("id", ""))
            if not model_id:
                continue
            eid = stable_id("model_profile", provider.ref + ":" + model_id)
            payload = {
                "id": eid,
                "name": provider.name,
                "url": provider.base_url or "",
                "model": model_id,
                "syncSecret": provider.ref in secrets,
                "_xfpack": {
                    "provider": provider.model_dump(mode="json", exclude={"has_secret"}),
                    "model_id": model_id,
                },
            }
            if provider.ref in secrets:
                payload["key"] = secrets[provider.ref]
            result.append(Entity(entity_type="model_profile", entity_id=eid, payload=payload))
    return result


async def stage_configuration(
    sync: CustomerSyncService,
    packs: PortablePackService,
    user: User,
    space: str,
    selection: ExportSelection,
) -> Journal:
    if selection.include_secrets or selection.provider_ids or selection.voice_provider_ids:
        packs._manage_models(user)
    package, files, secrets = await packs._snapshot(user, selection)
    try:
        entities = await asyncio.get_running_loop().run_in_executor(
            None, entities_from_pack, package, files, secrets
        )
    except (ValueError, KeyError) as exc:
        raise sync.error(user) from exc
    # Provider descriptors embedded in experts preserve bindings without allowing
    # a non-manager to publish editable provider profiles.
    if not (await packs.inventory(user))["can_manage_models"]:
        entities = [e for e in entities if e.entity_type != "model_profile"]

    def apply(journal: Journal) -> None:
        for entity in entities:
            previous = next(
                (
                    e
                    for e in journal.entities
                    if e.entity_type == entity.entity_type and e.entity_id == entity.entity_id
                ),
                None,
            )
            if previous and not previous.deleted and previous.payload == entity.payload:
                continue
            sync.stage(
                user,
                journal,
                Edit(
                    **entity.model_dump(exclude={"revision"}),
                    revision=previous.revision if previous else 0,
                    sync_secret=entity.payload.get("syncSecret") is True,
                ),
            )

    return await sync.transact(user, space, apply)


def pack_from_entities(entities: list[Entity]) -> bytes:
    """Download a key-free package for the existing inspect/plan/commit flow.

    This does not install skills, mutate providers, or bypass model permissions.
    Existing xfpack resources retain files, team bindings and dependencies.
    """
    experts: list[Expert] = []
    providers: dict[str, Provider] = {}
    files: dict[str, bytes] = {}
    live = [e for e in entities if not e.deleted]
    ref_by_id: dict[str, str] = {}
    for entity in live:
        meta: Any = entity.payload.get("_xfpack", {})
        if entity.entity_type in {"expert", "team"}:
            resource = meta.get("resource", {})
            ref_by_id[entity.entity_id] = resource.get("ref") or "expert:" + stable_id(
                "expert", entity.entity_id
            )
    for entity in sorted(live, key=lambda e: e.entity_type == "model_profile"):
        p: Any = entity.payload
        meta = p.get("_xfpack", {})
        if entity.entity_type in {"expert", "team"}:
            resource = dict(meta.get("resource", {}))
            resource.update(
                ref=ref_by_id[entity.entity_id],
                name=p["name"],
                kind=entity.entity_type,
                system_prompt=p.get("prompt", ""),
            )
            if entity.entity_type == "team":
                resource["member_refs"] = [ref_by_id[member] for member in p.get("members", [])]
            for provider in meta.get("providers", []):
                parsed = Provider.model_validate({**provider, "has_secret": False})
                providers[parsed.ref] = parsed
            for path, content in meta.get("files", {}).items():
                decoded = base64.b64decode(content, validate=True)
                path = safe_path(path)
                if path in files and files[path] != decoded:
                    raise ValueError("conflicting asset")
                files[path] = decoded
            resource_files = dict(resource.get("files", {}))
            for skill_id in p.get("skills", []):
                skill = next(
                    e for e in live if e.entity_type == "skill" and e.entity_id == skill_id
                )
                slug = stable_id("skill", skill_id)
                relative = f"skills/{slug}/SKILL.md"
                path = f"assets/{stable_id('expert', entity.entity_id)}/{relative}"
                original = next(
                    (
                        relative
                        for relative in resource_files
                        if relative.startswith("skills/")
                        and relative.endswith("/SKILL.md")
                        and stable_id("skill", resource["ref"] + ":" + relative) == skill_id
                    ),
                    None,
                )
                if original:
                    files[resource_files[original]] = str(skill.payload["content"]).encode()
                else:
                    resource_files[relative] = path
                    files[path] = str(skill.payload["content"]).encode()
            for knowledge_id in p.get("knowledge", []):
                knowledge = next(
                    e for e in live if e.entity_type == "knowledge" and e.entity_id == knowledge_id
                )
                relative = f"knowledge/{stable_id('knowledge', knowledge_id)}.md"
                path = f"assets/{stable_id('expert', entity.entity_id)}/{relative}"
                resource_files[relative] = path
                files[path] = str(knowledge.payload["content"]).encode()
            catalog = p.get("catalog", {})
            if catalog:
                resource["dependencies"] = list(
                    dict.fromkeys(
                        [
                            *resource.get("dependencies", []),
                            *[v for v in catalog.get("dependencies", []) if isinstance(v, str)],
                        ]
                    )
                )
                for relative, content in catalog.get("files", {}).items():
                    if isinstance(content, str):
                        relative = safe_path(relative)
                        path = f"assets/{stable_id('expert', entity.entity_id)}/{relative}"
                        resource_files[relative] = path
                        files[path] = content.encode()
            resource["files"] = resource_files
            experts.append(Expert.model_validate(resource))
        elif entity.entity_type == "model_profile":
            raw = meta.get("provider") or {
                "ref": "text:" + stable_id("model", entity.entity_id),
                "name": p["name"],
                "kind": "openai",
                "base_url": p["url"],
                "models": [{"id": p["model"]}],
            }
            current = providers.get(raw["ref"])
            models = [dict(m) for m in (current.models if current else raw.get("models", []))]
            old_id = meta.get("model_id", p["model"])
            selected_model = next((m for m in models if m.get("id") == old_id), None)
            if selected_model is not None:
                selected_model["id"] = p["model"]
            elif not any(m.get("id") == p["model"] for m in models):
                models.append({"id": p["model"]})
            provider = Provider.model_validate(
                {
                    **raw,
                    "name": p["name"],
                    "base_url": p["url"],
                    "models": models,
                    "has_secret": False,
                }
            )
            providers[provider.ref] = provider
    if not experts and not providers:
        raise ValueError("no portable resources")
    package = Package(
        package_id=uuid.uuid4().hex,
        client_version="customer-sync-v2",
        experts=experts,
        providers=list(providers.values()),
    )
    return encode(package, files, {}, "")


async def configuration_package(sync: CustomerSyncService, user: User, space: str) -> bytes:
    journal = await sync.transact(user, space)
    try:
        return await asyncio.get_running_loop().run_in_executor(
            None, pack_from_entities, journal.entities
        )
    except (ValueError, KeyError, StopIteration, TypeError, AttributeError) as exc:
        raise sync.error(user) from exc
