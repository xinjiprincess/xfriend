"""Adopt a signed identity only in its dedicated desktop account-space home."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from pydantic import ValidationError

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing.service import SignedLicense, product_license


async def adopt_cloud_identity(server: Any, lease: SignedLicense, bridge: str) -> Any:
    expected = os.environ.get("XFRIEND_CLOUD_BRIDGE_SECRET", "")
    if not expected or not secrets.compare_digest(expected, bridge):
        raise OctopError(ErrorCode.AUTH_FAILED, "untrusted desktop bridge")
    home = Path(server.paths.root)
    licensing = product_license(home)
    async with licensing.lock:
        await licensing._load()
        if licensing.profile is None or not licensing.profile.cloud_accounts:
            raise OctopError(ErrorCode.AUTH_FAILED, "cloud accounts not enabled")
        try:
            entitlement = licensing._verify(lease)
        except (ValueError, InvalidSignature, ValidationError) as exc:
            raise OctopError(ErrorCode.AUTH_FAILED, "invalid cloud lease") from exc
        key = hashlib.sha256(
            (
                licensing.profile.server_url
                + "\x00"
                + entitlement.user_id
                + "\x00"
                + entitlement.space_id
            ).encode()
        ).hexdigest()
        if key != os.environ.get("XFRIEND_CLOUD_SPACE_KEY") or home.name != key:
            raise OctopError(ErrorCode.AUTH_FAILED, "account space mismatch")
        now = int(time.time())
        if (
            not entitlement.not_before
            <= now
            < min(entitlement.expires_at, entitlement.offline_until or 0)
        ):
            raise OctopError(ErrorCode.AUTH_FAILED, "cloud lease expired")
        licensing.record = {
            "license": lease.model_dump(),
            "last_seen": now,
            "last_synced_at": entitlement.issued_at,
        }
        await licensing._save()
    username = "cloud_" + hashlib.sha256(entitlement.user_id.encode()).hexdigest()[:24]
    user = server.user_manager.get(username)
    if user is None:
        # Administrator is scoped to an otherwise empty, isolated local home;
        # it does not confer platform or enterprise administration privileges.
        if server.user_manager.count():
            raise OctopError(
                ErrorCode.AUTH_FAILED, "existing local data requires explicit migration"
            )
        user = await server.user_manager.create(
            username=username,
            password=secrets.token_urlsafe(32),
            role="admin",
            display_name=entitlement.display_name,
            locale="zh",
        )
    return user
