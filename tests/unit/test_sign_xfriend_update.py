"""Publisher checks on real artifact bytes and key separation."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.sign_xfriend_update import sign_release


def test_signs_actual_artifact_and_rejects_traversal(tmp_path):  # type: ignore[no-untyped-def]
    artifact = tmp_path / "setup.exe"
    artifact.write_bytes(b"signed install payload")
    key = Ed25519PrivateKey.generate()
    spec = {
        "format": 1,
        "application": "xfriend",
        "platform": "windows-amd64",
        "channel": "stable",
        "version": "1.5.0",
        "sequence": 19,
        "expires": "2026-12-01T00:00:00Z",
        "minimum_updater": 1,
        "installer": {
            "id": "installer",
            "url": "https://example.com/setup.exe",
            "file": "setup.exe",
        },
    }
    envelope = json.loads(sign_release(spec, tmp_path, key, "release"))
    payload = base64.b64decode(envelope["payload"])
    signature = base64.b64decode(envelope["signature"])
    key.public_key().verify(signature, payload)
    release = json.loads(payload)
    assert release["installer"]["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert release["installer"]["size"] == artifact.stat().st_size
    assert "file" not in release["installer"]
    spec["installer"]["file"] = "../setup.exe"
    with pytest.raises(ValueError, match="Unsafe"):
        sign_release(spec, tmp_path, key, "release")
