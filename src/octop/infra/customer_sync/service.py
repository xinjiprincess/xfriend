"""Durable user/space journal. Network calls stay in the native Go proxy.

One settings value is the commit boundary for entities, cursor and outbox.
Operations are immutable until acknowledged: a lost response can safely retry.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Callable
from typing import Any

from octop.i18n import tr
from octop.infra.customer_sync.models import (
    Edit,
    Entity,
    Journal,
    Operation,
    PullResponse,
    PushResponse,
    Resolution,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User
from octop.infra.users.permissions import user_has_permission


def identity(entity: Entity | Operation) -> tuple[str, str]:
    return entity.entity_type, entity.entity_id


class CustomerSyncService:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.lock = asyncio.Lock()

    def error(self, user: User, code: ErrorCode = ErrorCode.SLASH_BAD_ARGS) -> OctopError:
        if code == ErrorCode.SLASH_BAD_ARGS:
            return OctopError(code, tr("transfer.invalid", user.locale))
        return OctopError.localized(code, user.locale)

    async def transact(
        self, user: User, space: str, change: Callable[[Journal], None] | None = None
    ) -> Journal:
        key = f"customer_sync:{user.id}:" + hashlib.sha256(space.encode()).hexdigest()
        async with self.lock:

            def work() -> Journal:
                raw = self.services.settings_repo.get(key)
                journal = Journal.model_validate_json(raw) if raw else Journal()
                if change:
                    change(journal)
                    self.services.settings_repo.set(key, journal.model_dump_json())
                return journal

            future = asyncio.get_running_loop().run_in_executor(None, work)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # Keep the lock until the durable commit finishes even when the
                # HTTP client disconnects; the next request must see that commit.
                await future
                raise

    def stage(self, user: User, journal: Journal, edit: Edit) -> None:
        if edit.source == "ai" and not edit.confirmed:
            raise self.error(user)
        if any(identity(op) == identity(edit) for op in journal.pending):
            raise self.error(user)
        previous = next((e for e in journal.entities if identity(e) == identity(edit)), None)
        if edit.revision != (previous.revision if previous else 0):
            raise self.error(user)
        payload = dict(edit.payload)
        if not edit.deleted:
            name = (
                "name"
                if edit.entity_type
                in {"customer", "expert", "team", "skill", "knowledge", "model_profile"}
                else "title"
            )
            if not isinstance(payload.get(name), str) or not str(payload[name]).strip():
                raise self.error(user)
            if edit.entity_type in {"communication", "followup", "opportunity"}:
                customer_id = payload.get("customerId")
                if customer_id != "" and not any(
                    e.entity_type == "customer" and e.entity_id == customer_id and not e.deleted
                    for e in journal.entities
                ):
                    raise self.error(user)
            payload["id"] = edit.entity_id
        if edit.entity_type == "model_profile":
            if not user_has_permission(user, "providers"):
                raise self.error(user, ErrorCode.FORBIDDEN)
            # Both generations of mobile payloads are accepted. Consent comes
            # from this edit, never from a previously downloaded payload.
            payload["syncSecret"] = edit.sync_secret
            payload["sync_secrets"] = edit.sync_secret
            if not edit.sync_secret:
                payload.pop("key", None)
                payload.pop("apiKey", None)
        if edit.deleted:
            payload = {}
        if edit.deleted and edit.entity_type == "customer":
            children = [
                e
                for e in journal.entities
                if not e.deleted
                and e.entity_type in {"communication", "followup", "opportunity"}
                and e.payload.get("customerId") == edit.entity_id
            ]
            # Never rewrite an immutable operation that may already be in flight.
            if any(identity(op) == identity(child) for op in journal.pending for child in children):
                raise self.error(user)
            for child in children:
                detached = child.model_copy(update={"payload": {**child.payload, "customerId": ""}})
                journal.pending.append(
                    Operation(
                        operation_id=str(uuid.uuid4()),
                        base_revision=child.revision,
                        **detached.model_dump(exclude={"revision"}),
                    )
                )
                self.replace(journal, detached)
        operation = Operation(
            operation_id=str(uuid.uuid4()),
            entity_type=edit.entity_type,
            entity_id=edit.entity_id,
            base_revision=edit.revision,
            deleted=edit.deleted,
            payload=payload,
        )
        journal.pending.append(operation)
        self.replace(
            journal,
            Entity(
                **operation.model_dump(exclude={"operation_id", "base_revision"}),
                revision=edit.revision,
            ),
        )

    @staticmethod
    def replace(journal: Journal, entity: Entity) -> None:
        journal.entities = [e for e in journal.entities if identity(e) != identity(entity)] + [
            entity
        ]

    async def edit(self, user: User, space: str, edit: Edit) -> Journal:
        return await self.transact(user, space, lambda j: self.stage(user, j, edit))

    async def acknowledge(self, user: User, space: str, response: PushResponse) -> Journal:
        def apply(journal: Journal) -> None:
            for result in response.results:
                op = next(
                    (o for o in journal.pending if o.operation_id == result.operation_id), None
                )
                if op is None:
                    continue
                if result.status == "conflict":
                    server = result.server_entity
                    if server is None and result.revision == 0:
                        server = Entity(
                            entity_type=op.entity_type, entity_id=op.entity_id, deleted=True
                        )
                    if (
                        server is None
                        or identity(server) != identity(op)
                        or server.revision != result.revision
                    ):
                        raise self.error(user)
                    shadow = journal.remote.pop(op.operation_id, None)
                    prior = journal.conflicts.get(op.operation_id)
                    journal.conflicts[op.operation_id] = max(
                        (e for e in (server, shadow, prior) if e is not None),
                        key=lambda e: e.revision,
                    )
                    continue
                if result.revision <= op.base_revision:
                    raise self.error(user)
                existing = next(e for e in journal.entities if identity(e) == identity(op))
                self.replace(journal, existing.model_copy(update={"revision": result.revision}))
                shadow = journal.remote.pop(op.operation_id, None)
                if shadow and shadow.revision >= result.revision:
                    self.replace(journal, shadow)
                journal.pending.remove(op)
                journal.conflicts.pop(op.operation_id, None)
            # Push cursor is deliberately ignored: it could skip other devices' changes.

        return await self.transact(user, space, apply)

    async def ingest(self, user: User, space: str, response: PullResponse) -> Journal:
        def apply(journal: Journal) -> None:
            if response.cursor < journal.cursor or (
                response.has_more and response.cursor <= journal.cursor
            ):
                raise self.error(user)
            for incoming in response.changes:
                op = next((o for o in journal.pending if identity(o) == identity(incoming)), None)
                if op:
                    # A lost push acknowledgement must retry its operation_id before
                    # treating a pull echo as a conflict or creating another operation.
                    previous_remote = journal.remote.get(op.operation_id)
                    if previous_remote is None or incoming.revision >= previous_remote.revision:
                        journal.remote[op.operation_id] = incoming
                    conflict = journal.conflicts.get(op.operation_id)
                    if conflict and incoming.revision >= conflict.revision:
                        journal.conflicts[op.operation_id] = incoming
                    continue
                old = next((e for e in journal.entities if identity(e) == identity(incoming)), None)
                if old is None or incoming.revision >= old.revision:
                    self.replace(journal, incoming)
            journal.cursor = response.cursor

        return await self.transact(user, space, apply)

    async def resolve(self, user: User, space: str, request: Resolution) -> Journal:
        def apply(journal: Journal) -> None:
            server = journal.conflicts.get(request.operation_id)
            op = next((o for o in journal.pending if o.operation_id == request.operation_id), None)
            if server is None or op is None:
                raise self.error(user)
            journal.pending.remove(op)
            del journal.conflicts[request.operation_id]
            journal.remote.pop(request.operation_id, None)
            self.replace(journal, server)
            if request.choice == "local":
                self.stage(
                    user,
                    journal,
                    Edit(
                        entity_type=op.entity_type,
                        entity_id=op.entity_id,
                        revision=server.revision,
                        payload=op.payload,
                        deleted=op.deleted,
                        sync_secret=op.payload.get("syncSecret") is True
                        or op.payload.get("sync_secrets") is True,
                    ),
                )

        return await self.transact(user, space, apply)
