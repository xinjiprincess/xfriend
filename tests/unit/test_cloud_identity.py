from __future__ import annotations

import asyncio
import hashlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.exceptions import InvalidSignature

from octop.infra.errors import OctopError
from octop.infra.licensing.service import SignedLicense
from octop.infra.users import cloud_identity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["valid", "wrong_space", "untrusted", "invalid_signature", "legacy"]
)
async def test_cloud_identity_is_bound_to_an_authenticated_space(tmp_path, monkeypatch, case):
    now = int(time.time())
    profile = SimpleNamespace(cloud_accounts=True, server_url="https://accounts.example.com")
    claims = SimpleNamespace(
        user_id="user",
        space_id="space",
        not_before=now - 1,
        expires_at=now + 86400,
        offline_until=now + 3600,
        issued_at=now - 1,
        display_name="测试",
    )
    key = hashlib.sha256((profile.server_url + "\x00user\x00space").encode()).hexdigest()
    home = tmp_path / ("different" if case == "wrong_space" else key)
    licensing = SimpleNamespace(
        lock=asyncio.Lock(),
        _load=AsyncMock(),
        _save=AsyncMock(),
        profile=profile,
        _verify=Mock(return_value=claims),
    )
    if case == "invalid_signature":
        licensing._verify.side_effect = InvalidSignature()
    manager = SimpleNamespace(
        get=Mock(return_value=None),
        count=Mock(return_value=case == "legacy"),
        create=AsyncMock(return_value="scoped-user"),
    )
    server = SimpleNamespace(paths=SimpleNamespace(root=home), user_manager=manager)
    monkeypatch.setattr(cloud_identity, "product_license", lambda _: licensing)
    monkeypatch.setenv("XFRIEND_CLOUD_BRIDGE_SECRET", "bridge-secret")
    monkeypatch.setenv("XFRIEND_CLOUD_SPACE_KEY", key)
    lease = SignedLicense(payload="test", signature="test")
    if case == "valid":
        assert (
            await cloud_identity.adopt_cloud_identity(server, lease, "bridge-secret")
            == "scoped-user"
        )
        assert manager.create.call_args.kwargs["username"].startswith("cloud_")
    else:
        with pytest.raises(OctopError):
            await cloud_identity.adopt_cloud_identity(
                server, lease, "wrong" if case == "untrusted" else "bridge-secret"
            )
        manager.create.assert_not_awaited()
        if case != "legacy":
            licensing._save.assert_not_awaited()
